"""The call path for swaps through the vault: owner config, guardian approval, agent spend, status.

Each function builds one near CLI command (a list, never a shell string). `execute` checks the network guard
first, prints the plan with private values scrubbed, and sends only with `confirmed=True`.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import time
from decimal import Decimal, localcontext
from typing import Callable, Mapping

from .chain import STDOUT_SENTINEL, ChainError, is_valid_account_id, near_to_yocto, parse_call_output, run_near
from .config import Config
from .guard import GuardError, check as guard_check
from .redact import scrub

MAX_APPROVAL_MINUTES = 29  # the contract accepts at most 30 minutes
QUOTE_HASH_RE = re.compile(r"[0-9a-f]{64}")
REHEARSAL_QUOTE_HASH = hashlib.sha256(b"leash-rehearsal-no-quote").hexdigest()  # testnet only: 1Click has no testnet
ONE_YOCTO = "1 yoctoNEAR"
NO_DEPOSIT = "0 NEAR"
OWNER_GAS = "30 Tgas"
AGENT_CAP_METHODS = ("approve_intent", "spend_intent")  # the plan shows the agent's caps next to these
RESULT_EVENTS = {"approve_intent": "intent_approved", "set_intents_config": "intents_configured", "set_routes": "routes_set"}
SWAP_NOTE = "the swap itself is shown by the wNEAR receipts and the vault balance, not by this line"


class CallPathError(Exception):
    pass


def _tx(cfg: Config, signer: str, method: str, payload: object, gas: str, deposit: str) -> list[str]:
    return [
        "near", "contract", "call-function", "as-transaction", cfg.vault, method,
        "json-args", json.dumps(payload, separators=(",", ":")),
        "prepaid-gas", gas, "attached-deposit", deposit,
        "sign-as", signer, "network-config", cfg.network, "sign-with-legacy-keychain", "send",
    ]


def _view(cfg: Config, method: str, payload: object | None = None) -> list[str]:
    return [
        "near", "contract", "call-function", "as-read-only", cfg.vault, method,
        "json-args", json.dumps(payload or {}, separators=(",", ":")),
        "network-config", cfg.network, "now",
    ]


def _account(value: str, what: str) -> str:
    if not is_valid_account_id(value):
        raise CallPathError(f"{what} is not a valid NEAR account id")
    return value


def set_intents_config_args(cfg: Config) -> list[str]:
    """Owner: set the wNEAR contract and the guardian (1 yocto). Revokes every pending approval."""
    payload = {"wnear": _account(cfg.wnear, "wnear"), "guardian": _account(cfg.guardian, "guardian")}
    return _tx(cfg, cfg.owner, "set_intents_config", payload, OWNER_GAS, ONE_YOCTO)


def set_routes_args(cfg: Config, dest_asset: str, recipient: str) -> list[str]:
    """Owner: set the agent's one route (id 0). The recipient comes from the environment and is never printed."""
    if not dest_asset or not recipient:
        raise CallPathError("set LEASH_ROUTE_DEST_ASSET and LEASH_ROUTE_RECIPIENT (see .env.example)")
    payload = {"agent": cfg.agent, "routes": [{"dest_asset": dest_asset, "recipient": recipient}]}
    return _tx(cfg, cfg.owner, "set_routes", payload, OWNER_GAS, ONE_YOCTO)


def approve_intent_args(
    cfg: Config, route_id: int, deposit_account: str, amount_near: str, minutes: int, quote_hash: str,
    now_ns: int | None = None,
) -> list[str]:
    """Guardian: one single-use approval, signed with the guardian's own key, no deposit."""
    if not 0 <= route_id <= 254:
        raise CallPathError("route id must be from 0 to 254")
    if not 1 <= minutes <= MAX_APPROVAL_MINUTES:
        raise CallPathError(f"minutes must be from 1 to {MAX_APPROVAL_MINUTES}")
    if not QUOTE_HASH_RE.fullmatch(quote_hash):
        raise CallPathError("quote hash must be 64 lowercase hex characters")
    now = time.time_ns() if now_ns is None else now_ns
    payload = {
        "agent": cfg.agent, "route_id": route_id,
        "deposit_account": _account(deposit_account, "deposit account"),
        "amount": near_to_yocto(amount_near),
        "expires_ns": str(now + minutes * 60 * 1_000_000_000),
        "quote_hash": quote_hash,
    }
    return _tx(cfg, cfg.guardian, "approve_intent", payload, OWNER_GAS, NO_DEPOSIT)


def spend_intent_args(cfg: Config, route_id: int, deposit_account: str, amount_near: str) -> list[str]:
    """Agent: spend one approval. Uses its own gas setting, never the 30 Tgas of `spend`."""
    if not 0 <= route_id <= 254:
        raise CallPathError("route id must be from 0 to 254")
    payload = {"route_id": route_id, "deposit_account": _account(deposit_account, "deposit account"), "amount": near_to_yocto(amount_near)}
    return _tx(cfg, cfg.agent, "spend_intent", payload, cfg.intent_gas, NO_DEPOSIT)


def status_commands(cfg: Config) -> list[tuple[str, list[str]]]:
    """Read-only views: approvals, stats, policy counters, vault balance, routes (count shown, never the recipient)."""
    return [
        ("get_approvals", _view(cfg, "get_approvals", {"agent": cfg.agent})),
        ("get_stats", _view(cfg, "get_stats")),
        ("get_policy", _view(cfg, "get_policy", {"agent": cfg.agent})),
        ("get_available_balance", _view(cfg, "get_available_balance")),
    ]


def _signer(args: list[str]) -> str:
    return args[args.index("sign-as") + 1]


def yocto_to_near(value: object) -> str:
    """Exact NEAR text for a yoctoNEAR amount, without trailing zeros."""
    with localcontext() as ctx:
        ctx.prec = 80
        text = format(Decimal(int(str(value))).scaleb(-24), "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


def utc_text(ns: int) -> str:
    moment = datetime.datetime.fromtimestamp(ns / 1e9, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _payload_of(args: list[str]) -> dict:
    try:
        value = json.loads(args[args.index("json-args") + 1])
    except (ValueError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def plan_details(args: list[str]) -> list[str]:
    """What will be sent, read from the call's own arguments. The route recipient is never shown."""
    method, data = args[5], _payload_of(args)
    if method in AGENT_CAP_METHODS:
        line = f"route {data.get('route_id')}, deposit account {data.get('deposit_account')}, amount {yocto_to_near(data.get('amount', 0))} NEAR"
        if method == "approve_intent":
            line += f", approval expires {utc_text(int(data['expires_ns']))}"
        return [line]
    if method == "set_intents_config":
        return [f"wNEAR contract {data.get('wnear')}, guardian {data.get('guardian')}; every pending approval stops matching"]
    if method == "set_routes":
        return [f"{len(data.get('routes', []))} route(s) for {data.get('agent')}; asset {data['routes'][0].get('dest_asset')}, recipient not shown"]
    return []


def caps_line(cfg: Config, runner: Callable[[list[str]], str]) -> str | None:
    """The agent's caps and what is already used, from `get_policy`. None if they cannot be read."""
    try:
        policy = parse_call_output(runner(_view(cfg, "get_policy", {"agent": cfg.agent}))).returned
        return (f"agent {cfg.agent} caps: per-tx {yocto_to_near(policy['per_tx_cap'])} NEAR, daily {yocto_to_near(policy['daily_cap'])} NEAR, "
                f"already used in this window {yocto_to_near(policy['spent_in_window'])} NEAR")
    except (ChainError, KeyError, TypeError, ValueError):
        return None


def _event_reason(events: list[dict], name: str) -> str | None:
    for event in reversed(events):
        if event.get("event") == name:
            data = event.get("data") or [{}]
            return str(data[0].get("reason")) if isinstance(data[0], dict) and data[0].get("reason") else None
    return None


def _panic_text(raw: str) -> str:
    for line in raw.splitlines():
        if re.search(r"panick|ExecutionError|Failure", line, re.I):
            return scrub(line.strip(" │├└─-\t"))[:300]
    return "no panic message in the output (the CLI output of a failed on-chain call is unverified)"


def interpret(method: str, raw: str) -> str:
    """One line for the screen: ALLOWED, DENIED or FAILED for `spend_intent`; done or unconfirmed for the others."""
    result = parse_call_output(raw)
    names = {event.get("event") for event in result.events}
    if method == "spend_intent":
        if result.returned is True and "intent_sent" in names:
            return f"ALLOWED: batch scheduled. Note: {SWAP_NOTE}"
        if result.returned is False or "intent_denied" in names:
            return f"DENIED: {_event_reason(result.events, 'intent_denied') or 'reason not found in the output'}. Note: {SWAP_NOTE}"
        return f"FAILED: {_panic_text(raw)}. Note: {SWAP_NOTE}"
    event = RESULT_EVENTS.get(method)
    if event and event in names:
        return f"DONE: the {event} event was seen"
    if event:
        return f"UNCONFIRMED: no {event} event in the output ({_panic_text(raw)}); check with make intents-status"
    return "no result line for this method"


def require_vault_name(cfg: Config, given: str | None, confirmed: bool) -> None:
    """Owner commands that send must be given the vault name, so a wrong environment cannot send by habit."""
    if not confirmed:
        return
    if not given:
        raise CallPathError("owner commands need --confirm-vault with the vault name (make: CONFIRM_VAULT=<vault>)")
    if given != cfg.vault:
        raise CallPathError("--confirm-vault does not match the vault of this configuration; nothing sent")


def guard_or_raise(cfg: Config, accounts: list[str], env: Mapping[str, str] | None = None) -> None:
    try:
        guard_check(cfg.network, accounts, env)
    except GuardError as exc:
        raise CallPathError(str(exc)) from exc


def execute(
    cfg: Config, args: list[str], accounts: list[str], *, confirmed: bool,
    runner: Callable[[list[str]], str] = run_near, env: Mapping[str, str] | None = None, out=print,
    extra: tuple[str, ...] | list[str] = (),
    before_send: Callable[[], None] | None = None, on_failure: Callable[[], None] | None = None,
) -> str | None:
    """Guard, show the plan, and send only when confirmed. Returns the scrubbed output, or None for a plan only.

    `before_send` runs after every refusal, immediately before the transaction is handed to the CLI; `on_failure` runs if the CLI fails.
    """
    guard_or_raise(cfg, accounts, env)
    method, sends = args[5], args[3] == "as-transaction"
    if sends:
        gas = args[args.index("prepaid-gas") + 1]
        deposit = args[args.index("attached-deposit") + 1]
        out(scrub(f"{cfg.network}: {method} on {cfg.vault}, signed by {_signer(args)}, prepaid gas {gas}, deposit {deposit}"))
        for line in plan_details(args):
            out(scrub(line))
        if method in AGENT_CAP_METHODS:
            caps = caps_line(cfg, runner)
            out(caps or "agent caps: could not be read")
            if caps is None and confirmed and cfg.network != "testnet":
                raise CallPathError("the agent's caps could not be read, so nothing was sent")
        for line in extra:
            out(scrub(line))
        if not confirmed:
            out("plan only: nothing sent. Add --yes to send.")
            return None
    if sends and before_send is not None:
        before_send()
    try:
        raw = runner(args)
    except ChainError as exc:
        if sends and on_failure is not None:
            on_failure()
        raise CallPathError(scrub(f"FAILED: {exc} (the CLI exit code of a failed on-chain call is unverified; check the explorer)")) from exc
    text = scrub(raw.replace(STDOUT_SENTINEL, "\n"))
    return f"{text}\nRESULT: {scrub(interpret(method, raw))}" if sends else text
