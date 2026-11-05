"""Calls to the LEASH vault through the near CLI, and parsing of its output."""

from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from typing import Callable

from .config import Config
from .redact import scrub

RETURN_MARKER = "Function execution return value (printed to stdout):"
EVENT_PREFIX = "EVENT_JSON:"
# run_near joins stderr and stdout with this marker, so the parser can tell them apart.
STDOUT_SENTINEL = "\n@@LEASH-STDOUT@@\n"
MAX_MEMO_BYTES = 100  # the vault accepts 128; keep some room

_ACCOUNT_RE = re.compile(r"[a-z0-9]+(?:[_.-][a-z0-9]+)*")
_AMOUNT_RE = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,24})?")
TX_ID_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{43,44}")
# A real id sits alone on its own line after the box characters. The echoed call arguments
# (which include the memo) never match, so a hostile memo cannot fake the id.
_TX_LINE_RE = re.compile(r"^[\s│├└─-]*Transaction ID:\s*([1-9A-HJ-NP-Za-km-z]{43,44})\s*$", re.M)
_LINE_PREFIX_CHARS = " \t│├└─-"


def is_valid_account_id(value: object) -> bool:
    return isinstance(value, str) and 2 <= len(value) <= 64 and bool(_ACCOUNT_RE.fullmatch(value))


def clean_text(text: str, max_bytes: int | None = None) -> str:
    """Drop control characters and line breaks; optionally cut to at most `max_bytes` UTF-8 bytes."""
    kept = "".join(ch for ch in str(text) if not unicodedata.category(ch).startswith(("C", "Z")) or ch == " ")
    if max_bytes is None:
        return kept
    out, size = [], 0
    for ch in kept:
        width = len(ch.encode("utf-8"))
        if size + width > max_bytes:
            break
        out.append(ch)
        size += width
    return "".join(out)


class ChainError(Exception):
    """The vault call failed or its output could not be understood."""


def near_to_yocto(amount: str) -> str:
    """Convert a plain decimal NEAR amount (like 0.5) to an exact yoctoNEAR string."""
    text = str(amount).strip()
    if not _AMOUNT_RE.fullmatch(text):
        raise ValueError("amount must be a plain decimal like 0.5")
    with localcontext() as ctx:
        ctx.prec = 80
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"not a number: {amount!r}") from exc
        if not value.is_finite() or value <= 0:
            raise ValueError("amount must be positive")
        yocto = value.scaleb(24)
        if yocto != yocto.to_integral_value():
            raise ValueError("amount has more than 24 decimals")
        return str(int(yocto))


@dataclass
class CallResult:
    returned: object | None
    tx_id: str | None
    events: list[dict] = field(default_factory=list)
    raw: str = ""

    @property
    def allowed_event(self) -> bool:
        """True if the vault logged a `spend_allowed` event for this call."""
        return any(event.get("event") == "spend_allowed" for event in self.events)

    @property
    def denial_reason(self) -> str | None:
        for event in reversed(self.events):
            if event.get("event") == "spend_denied":
                data = event.get("data") or [{}]
                return data[0].get("reason")
        return None


def _value_after_marker(text: str) -> object | None:
    """Read the return value printed after the human-readable marker line."""
    if RETURN_MARKER not in text:
        return None
    tail = text.split(RETURN_MARKER, 1)[1]
    tail = tail.split("Here is your console command", 1)[0]
    tail = tail.split('The "', 1)[0].strip()
    if not tail:
        return None
    try:
        return json.loads(tail)
    except json.JSONDecodeError:
        return tail


def _value_from_stdout(text: str) -> object | None:
    """The near CLI prints the bare return value on stdout when it is not a terminal."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _value_after_marker(text)


def parse_call_output(text: str) -> CallResult:
    """Extract the return value, transaction id and leash events from near CLI output."""
    stdout_part = ""
    if STDOUT_SENTINEL in text:
        text, stdout_part = text.rsplit(STDOUT_SENTINEL, 1)
    combined = text + "\n" + stdout_part
    tx_match = _TX_LINE_RE.search(combined)
    events: list[dict] = []
    for line in combined.splitlines():
        stripped = line.lstrip(_LINE_PREFIX_CHARS)
        if stripped.startswith(EVENT_PREFIX):
            payload = stripped[len(EVENT_PREFIX):].strip()
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    returned = _value_from_stdout(stdout_part)
    if returned is None:
        returned = _value_after_marker(combined)
    return CallResult(returned, tx_match.group(1) if tx_match else None, events, combined)


def _tail(text: str, size: int = 600) -> str:
    return text.strip()[-size:]


@dataclass(frozen=True)
class Snapshot:
    """The vault state that decides what a payment by the agent can do (amounts in yoctoNEAR)."""

    per_tx_cap: int
    daily_cap: int
    spent: int
    allowlist: tuple[str, ...]
    agent_paused: bool
    all_paused: bool
    balance: int


Runner = Callable[[list[str]], str]


def run_near(args: list[str]) -> str:
    """Run the near CLI and return its combined output."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=180, check=False, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError as exc:
        raise ChainError("near CLI not found. Install near-cli-rs.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ChainError("near CLI timed out") from exc
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    if os.environ.get("LEASH_DEBUG"):
        print(f"--- near stderr ---\n{scrub(stderr)}\n--- near stdout ---\n{scrub(stdout)}")
    if proc.returncode != 0:
        raise ChainError(f"near CLI failed: {_tail(stderr + stdout, 500)}")
    return stderr + STDOUT_SENTINEL + stdout


class Vault:
    """Client for the vault contract, signing as the agent account."""

    def __init__(self, config: Config, runner: Runner = run_near) -> None:
        self.config = config
        self.runner = runner

    def spend(self, to: str, amount_near: str, memo: str = "") -> CallResult:
        """Ask the vault to pay. `returned` is True if scheduled, False if denied."""
        if not is_valid_account_id(to):
            raise ChainError("invalid destination account")
        payload = {"to": to, "amount": near_to_yocto(amount_near), "memo": clean_text(memo, MAX_MEMO_BYTES)}
        args = [
            "near", "contract", "call-function", "as-transaction", self.config.vault,
            "spend", "json-args", json.dumps(payload),
            "prepaid-gas", self.config.gas, "attached-deposit", "0 NEAR",
            "sign-as", self.config.agent,
            "network-config", self.config.network,
            "sign-with-legacy-keychain", "send",
        ]
        result = parse_call_output(self.runner(args))
        if not isinstance(result.returned, bool):
            raise ChainError(f"vault returned no result for spend. Output tail: {_tail(result.raw)}")
        return result

    def view(self, method: str, args: dict | None = None) -> object:
        cmd = [
            "near", "contract", "call-function", "as-read-only", self.config.vault,
            method, "json-args", json.dumps(args or {}),
            "network-config", self.config.network, "now",
        ]
        result = parse_call_output(self.runner(cmd))
        if result.returned is None:
            raise ChainError(f"no result from {method}. Output tail: {_tail(result.raw)}")
        return result.returned

    def snapshot(self) -> Snapshot:
        """Read the agent's policy, the pause flag and the vault balance (three views)."""
        try:
            policy = self.view("get_policy", {"agent": self.config.agent})
        except ChainError as exc:
            raise ChainError(
                f"cannot read the policy of {self.config.agent} in {self.config.vault} "
                f"(is one set? run make policy-testnet): {exc}"
            ) from exc
        if not isinstance(policy, dict):
            raise ChainError(f"{self.config.agent} has no policy in {self.config.vault}; run make policy-testnet")
        paused_all = self.view("is_paused_all")
        balance = self.view("get_available_balance")
        try:
            return Snapshot(
                per_tx_cap=int(policy["per_tx_cap"]),
                daily_cap=int(policy["daily_cap"]),
                spent=int(policy["spent_in_window"]),
                allowlist=tuple(str(a) for a in policy["allowlist"]),
                agent_paused=bool(policy["paused"]),
                all_paused=paused_all is True,
                balance=int(balance),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ChainError(f"unexpected vault view output: {exc!r}") from exc

    def stats(self) -> tuple[int, int]:
        allowed, denied = self.view("get_stats")  # type: ignore[misc]
        return int(allowed), int(denied)
