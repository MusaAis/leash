"""The call path for swaps through the vault: owner config, guardian approval, agent spend, status.

Each function builds one near CLI command (a list, never a shell string). `execute` checks the network guard
first, prints the plan with private values scrubbed, and sends only with `confirmed=True`.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Callable, Mapping

from .chain import STDOUT_SENTINEL, ChainError, is_valid_account_id, near_to_yocto, run_near
from .config import Config
from .guard import GuardError, check as guard_check
from .redact import scrub

MAX_APPROVAL_MINUTES = 29  # the contract accepts at most 30 minutes
QUOTE_HASH_RE = re.compile(r"[0-9a-f]{64}")
REHEARSAL_QUOTE_HASH = hashlib.sha256(b"leash-rehearsal-no-quote").hexdigest()  # testnet only: 1Click has no testnet
ONE_YOCTO = "1 yoctoNEAR"
NO_DEPOSIT = "0 NEAR"
OWNER_GAS = "30 Tgas"


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


def execute(
    cfg: Config, args: list[str], accounts: list[str], *, confirmed: bool,
    runner: Callable[[list[str]], str] = run_near, env: Mapping[str, str] | None = None, out=print,
) -> str | None:
    """Guard, show the plan, and send only when confirmed. Returns the scrubbed output, or None for a plan only."""
    try:
        guard_check(cfg.network, accounts, env)
    except GuardError as exc:
        raise CallPathError(str(exc)) from exc
    method = args[5]
    if args[3] == "as-transaction":
        gas = args[args.index("prepaid-gas") + 1]
        deposit = args[args.index("attached-deposit") + 1]
        out(scrub(f"{cfg.network}: {method} on {cfg.vault}, signed by {_signer(args)}, prepaid gas {gas}, deposit {deposit}"))
        if not confirmed:
            out("plan only: nothing sent. Add --yes to send.")
            return None
    try:
        raw = runner(args)
    except ChainError as exc:
        raise CallPathError(scrub(str(exc))) from exc
    return scrub(raw.replace(STDOUT_SENTINEL, "\n"))
