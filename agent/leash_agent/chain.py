"""Calls to the LEASH vault through the near CLI, and parsing of its output."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from typing import Callable

from .config import Config

RETURN_MARKER = "Function execution return value (printed to stdout):"
EVENT_PREFIX = "EVENT_JSON:"
# run_near joins stderr and stdout with this marker, so the parser can tell them apart.
STDOUT_SENTINEL = "\n@@LEASH-STDOUT@@\n"


class ChainError(Exception):
    """The vault call failed or its output could not be understood."""


def near_to_yocto(amount: str) -> str:
    """Convert a decimal NEAR amount to an exact yoctoNEAR string."""
    with localcontext() as ctx:
        ctx.prec = 80
        try:
            value = Decimal(str(amount).strip())
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
    tx_match = re.search(r"Transaction ID:\s*(\S+)", combined)
    events: list[dict] = []
    for line in combined.splitlines():
        if EVENT_PREFIX in line:
            payload = line.split(EVENT_PREFIX, 1)[1].strip()
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


Runner = Callable[[list[str]], str]


def run_near(args: list[str]) -> str:
    """Run the near CLI and return its combined output."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=180, check=False
        )
    except FileNotFoundError as exc:
        raise ChainError("near CLI not found. Install near-cli-rs.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ChainError("near CLI timed out") from exc
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    if os.environ.get("LEASH_DEBUG"):
        print(f"--- near stderr ---\n{stderr}\n--- near stdout ---\n{stdout}")
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
        payload = {"to": to, "amount": near_to_yocto(amount_near), "memo": memo}
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

    def stats(self) -> tuple[int, int]:
        allowed, denied = self.view("get_stats")  # type: ignore[misc]
        return int(allowed), int(denied)
