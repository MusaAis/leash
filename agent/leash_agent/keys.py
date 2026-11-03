"""Function-call keys for the guardian and the agent. Never creates or keeps an owner full-access key."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from .callpath import CallPathError, guard_or_raise, yocto_to_near
from .chain import ChainError, run_near
from .config import Config
from .redact import scrub

# Allowance in NEAR: guardian 0.02 (6 calls at 30 Tgas), agent 0.08 (5 calls at 150 Tgas), both at 0.0001 NEAR per Tgas.
GUARDIAN_METHODS, GUARDIAN_ALLOWANCE = ("approve_intent",), "0.02"
AGENT_METHODS, AGENT_ALLOWANCE = ("spend", "spend_intent"), "0.08"
AGENT_FUNDING_MAINNET = "0.10"
RPC_URLS = {"testnet": "https://test.rpc.fastnear.com", "mainnet": "https://free.rpc.fastnear.com"}  # docs.near.org/api/rpc/providers


def add_key_args(cfg: Config, account: str, methods: tuple[str, ...], allowance: str) -> list[str]:
    """One function-call key, receiver = the vault, signed by the account itself. Syntax unverified: see `near account add-key --help`."""
    return [
        "near", "account", "add-key", account, "grant-function-call-access",
        "--allowance", f"{allowance} NEAR", "--receiver-account-id", cfg.vault, "--method-names", ",".join(methods),
        "autogenerate-new-keypair", "save-to-legacy-keychain", "network-config", cfg.network,
        "sign-with-legacy-keychain", "send",
    ]


def plan(cfg: Config) -> list[tuple[str, tuple[str, ...], str]]:
    return [(cfg.guardian, GUARDIAN_METHODS, GUARDIAN_ALLOWANCE), (cfg.agent, AGENT_METHODS, AGENT_ALLOWANCE)]


def owner_key_files(cfg: Config, home: Path) -> list[Path]:
    """Key files of the owner account in the legacy keychain of this machine (other keychains are not searched)."""
    base = home / ".near-credentials" / cfg.network
    return [p for p in (base / f"{cfg.owner}.json", base / cfg.owner) if p.exists()]


def fetch_keys(account: str, network: str) -> object:
    url = os.environ.get("LEASH_RPC_URL") or RPC_URLS.get(network, "")
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ChainError(f"no https RPC endpoint for network {network!r}; set LEASH_RPC_URL")
    body = json.dumps({"jsonrpc": "2.0", "id": "leash", "method": "query",
                       "params": {"request_type": "view_access_key_list", "finality": "final", "account_id": account}}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChainError(f"RPC request failed: {exc}") from exc


def describe(answer: object, account: str, vault: str, methods: tuple[str, ...], allowance: str) -> tuple[list[str], bool]:
    """One line per key with its methods and allowance. The flag is True if any key is full access or not as planned."""
    try:
        keys = answer["result"]["keys"]  # type: ignore[index]
        rows = [(str(k["public_key"]), k["access_key"]["permission"]) for k in keys]
    except (KeyError, TypeError) as exc:
        raise ChainError(f"unreadable key list for {account}: {exc!r}") from exc
    lines, bad = [], False
    for public_key, permission in rows:
        call = permission.get("FunctionCall") if isinstance(permission, dict) else None
        if call is None:
            lines.append(f"{account} {public_key}: FULL ACCESS")
            bad = True
            continue
        raw_allowance = call.get("allowance")
        shown = "NO LIMIT" if raw_allowance is None else f"{yocto_to_near(raw_allowance)} NEAR"
        names = call.get("method_names") or []
        as_planned = call.get("receiver_id") == vault and sorted(names) == sorted(methods) and raw_allowance is not None \
            and int(raw_allowance) <= int(Decimal(allowance).scaleb(24))
        bad = bad or not as_planned
        lines.append(f"{account} {public_key}: function call, receiver {call.get('receiver_id')}, methods {','.join(names) or 'ALL'}, "
                     f"allowance {shown}{'' if as_planned else '  <-- NOT AS PLANNED'}")
    return lines, bad


def run(
    cfg: Config, *, confirmed: bool, runner: Callable[[list[str]], str] = run_near, env: Mapping[str, str] | None = None,
    out=print, fetch: Callable[[str, str], object] = fetch_keys, home: Path | None = None,
) -> int:
    """Plan, and with `confirmed` add the two keys, then print every key of both accounts read back from the RPC."""
    guard_or_raise(cfg, [cfg.vault, cfg.owner, cfg.guardian, cfg.agent], env)
    found = owner_key_files(cfg, home or Path.home())
    if found and cfg.network == "mainnet":
        raise CallPathError("an owner key file is on this machine; the owner key belongs on the owner's laptop only. Nothing was changed")
    if found:
        out("note: testnet only: an owner key file is on this machine; on mainnet this command refuses to run")
    out(f"{cfg.network}: function-call keys, receiver {cfg.vault}. No owner key is created or kept by this command.")
    for account, methods, allowance in plan(cfg):
        out(f"  {account}: methods {','.join(methods)}, allowance {allowance} NEAR")
    if cfg.network == "mainnet":
        out(f"  the agent account must hold about {AGENT_FUNDING_MAINNET} NEAR for gas (fund it from the owner, on the owner's laptop)")
    if not confirmed:
        out("plan only: nothing sent. Add --yes to send.")
        return 0
    for account, methods, allowance in plan(cfg):
        try:
            runner(add_key_args(cfg, account, methods, allowance))
        except ChainError as exc:
            raise CallPathError(scrub(f"adding the key for {account} failed: {exc}")) from exc
    problems = False
    for account, methods, allowance in plan(cfg):
        try:
            lines, bad = describe(fetch(account, cfg.network), account, cfg.vault, methods, allowance)
        except ChainError as exc:
            raise CallPathError(f"keys were sent but {account} could not be read back: {exc}") from exc
        for line in lines:
            out(line)
        problems = problems or bad
    if problems:
        out("A key is full access or not as planned. Remove it before this account is funded or used.")
        return 1 if cfg.network == "mainnet" else 0
    out("OK: only the planned function-call keys exist on both accounts.")
    return 0
