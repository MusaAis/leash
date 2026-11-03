"""Function-call keys for the guardian and the agent. Never creates or keeps an owner full-access key."""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from .callpath import CallPathError, guard_or_raise, yocto_to_near
from .chain import ChainError, run_near
from .config import Config
from .redact import scrub

# Allowance in NEAR. The node checks it against the reserved cost of a call, about 0.001 NEAR per prepaid Tgas plus 0.001 (measured on testnet).
# Guardian: two calls at 30 Tgas. Agent: two calls at 150 Tgas.
GUARDIAN_METHODS, GUARDIAN_ALLOWANCE = ("approve_intent",), "0.07"
AGENT_METHODS, AGENT_ALLOWANCE = ("spend", "spend_intent"), "0.31"
AGENT_FUNDING_MAINNET = "0.10"
RPC_URLS = {"testnet": "https://test.rpc.fastnear.com", "mainnet": "https://free.rpc.fastnear.com"}  # docs.near.org/api/rpc/providers


def add_key_args(cfg: Config, account: str, methods: tuple[str, ...], allowance: str) -> list[str]:
    """One function-call key, receiver = the vault, signed by the account itself.

    The scheme is passed explicitly so the CLI has no question left to ask.
    """
    return [
        "near", "account", "add-key", account, "grant-function-call-access",
        "--allowance", f"{allowance} NEAR", "--contract-account-id", cfg.vault, "--function-names", ",".join(methods),
        "autogenerate-new-keypair", "--signature-scheme", "ed25519", "save-to-legacy-keychain", "network-config", cfg.network,
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


KNOWN_ENV = "LEASH_KNOWN_FULL_ACCESS_KEYS"  # public keys only (not secrets), comma separated


def known_full_access(env: Mapping[str, str] | None) -> frozenset[str]:
    raw = (os.environ if env is None else env).get(KNOWN_ENV, "")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def describe(
    answer: object, account: str, vault: str, methods: tuple[str, ...], allowance: str, known: frozenset[str] = frozenset(),
) -> tuple[list[str], bool]:
    """One line per key with its methods and allowance. The flag is True if any key is full access (and not listed in `known`) or not as planned."""
    try:
        keys = answer["result"]["keys"]  # type: ignore[index]
        rows = [(str(k["public_key"]), k["access_key"]["permission"]) for k in keys]
    except (KeyError, TypeError) as exc:
        raise ChainError(f"unreadable key list for {account}: {exc!r}") from exc
    lines, bad = [], False
    for public_key, permission in rows:
        call = permission.get("FunctionCall") if isinstance(permission, dict) else None
        if call is None:
            listed = public_key in known
            lines.append(f"{account} {public_key}: FULL ACCESS" + (f" (listed in {KNOWN_ENV})" if listed else ""))
            bad = bad or not listed
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


def _on_chain(answer: object, account: str) -> dict[str, bool]:
    """Public key -> True if the key is full access, as read from the RPC answer."""
    try:
        return {str(k["public_key"]): not (isinstance(k["access_key"]["permission"], dict) and "FunctionCall" in k["access_key"]["permission"])
                for k in answer["result"]["keys"]}  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise ChainError(f"unreadable key list for {account}: {exc!r}") from exc


def local_key_files(account: str, network: str, home: Path) -> list[tuple[Path, str]]:
    """Key files of `account` in the legacy keychain of this machine with their public key. The secret part is never read out."""
    base = home / ".near-credentials" / network
    paths = [base / f"{account}.json"] + (sorted((base / account).glob("*.json")) if (base / account).is_dir() else [])
    found = []
    for path in paths:
        try:
            public = json.loads(path.read_text()).get("public_key")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(public, str):
            found.append((path, public))
    return found


def verify_local(cfg: Config, fetch: Callable[[str, str], object], home: Path, env: Mapping[str, str] | None, out) -> int:
    """Mainnet, read-only: the server may hold function-call keys as planned and no full-access key. Exit 1 on any problem."""
    known, problems = known_full_access(env), False
    for account, methods, allowance in plan(cfg):
        try:
            answer = fetch(account, cfg.network)
            lines, bad = describe(answer, account, cfg.vault, methods, allowance, known)
            chain = _on_chain(answer, account)
        except ChainError as exc:
            raise CallPathError(f"{account}: key list could not be read: {exc}") from exc
        for line in lines:
            out(line)
        problems = problems or bad
        files = local_key_files(account, cfg.network, home)
        usable = 0
        for path, public in files:
            if public not in chain:
                out(f"{account}: key file {path.name} holds a key that is not on the account")
            elif chain[public]:
                out(f"{account}: FULL-ACCESS KEY FILE on this machine ({path.name}). The server must hold function-call keys only")
                problems = True
            else:
                usable += 1
        if usable == 0:
            out(f"{account}: no function-call key file on this machine, so this machine cannot sign for it")
            problems = True
    out("PROBLEM: see above. Nothing was changed." if problems else "OK: this machine holds only function-call keys for the guardian and the agent, and no unknown full-access key is on either account.")
    return 1 if problems else 0


def move_aside(cfg: Config, answers: dict[str, object], home: Path) -> list[str]:
    """Testnet: move key files whose key is full access on the chain into a folder beside the keychain. Never deletes."""
    lines = []
    for account in answers:
        chain = _on_chain(answers[account], account)
        files = local_key_files(account, cfg.network, home)
        if not any(public in chain and not chain[public] for _, public in files):
            lines.append(f"{account}: no function-call key file found on this machine, so nothing was moved aside")
            continue
        target = home / ".near-credentials-aside" / cfg.network / account
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, 0o700)
        for path, public in files:
            if chain.get(public):
                destination = target / path.name
                n = 1
                while destination.exists():
                    destination, n = target / f"{path.name}.{n}", n + 1
                shutil.move(str(path), str(destination))
                lines.append(f"{account}: moved the full-access key file {path.name} to {target}")
    return lines


def link_account_file(cfg: Config, answers: dict[str, object], home: Path) -> list[str]:
    """After the creation key is moved aside, copy the one function-call key file to `<account>.json`, where the CLI looks first."""
    lines = []
    base = home / ".near-credentials" / cfg.network
    for account in answers:
        target = base / f"{account}.json"
        chain = _on_chain(answers[account], account)
        usable = [path for path, public in local_key_files(account, cfg.network, home) if chain.get(public) is False and path != target]
        if target.exists() or len(usable) != 1:
            continue
        shutil.copyfile(usable[0], target)
        os.chmod(target, 0o600)
        lines.append(f"{account}: copied the function-call key file to {target.name} so the CLI finds it")
    return lines


def run(
    cfg: Config, *, confirmed: bool, runner: Callable[[list[str]], str] = run_near, env: Mapping[str, str] | None = None,
    out=print, fetch: Callable[[str, str], object] = fetch_keys, home: Path | None = None, aside: bool = False,
) -> int:
    """Testnet: plan, and with `confirmed` add the two keys. Mainnet: read-only verification. Then every key is read back."""
    guard_or_raise(cfg, [cfg.vault, cfg.owner, cfg.guardian, cfg.agent], env)
    home = home or Path.home()
    found = owner_key_files(cfg, home)
    if found and cfg.network == "mainnet":
        raise CallPathError("an owner key file is on this machine; the owner key belongs on the owner's device only. Nothing was changed")
    if cfg.network == "mainnet":
        if confirmed or aside:
            raise CallPathError("on mainnet this command only verifies: the function-call keys are created on the owner's device. Nothing was changed")
        out("mainnet: read-only check of the guardian and agent keys. Nothing is added, moved or deleted.")
        return verify_local(cfg, fetch, home, env, out)
    if found:
        out("note: testnet only: an owner key file is on this machine; on mainnet this command refuses to run")
    out(f"{cfg.network}: function-call keys, receiver {cfg.vault}. No owner key is created or kept by this command.")
    for account, methods, allowance in plan(cfg):
        out(f"  {account}: methods {','.join(methods)}, allowance {allowance} NEAR")
    if not confirmed:
        out("plan only: nothing sent. Add --yes to send. The keys now on the accounts (read-only):")
        for account, methods, allowance in plan(cfg):
            try:
                lines, _ = describe(fetch(account, cfg.network), account, cfg.vault, methods, allowance)
            except ChainError as exc:
                out(f"  {account}: key list could not be read: {exc}")
                continue
            for line in lines:
                out(line)
        return 0
    for account, methods, allowance in plan(cfg):
        try:
            runner(add_key_args(cfg, account, methods, allowance))
        except ChainError as exc:
            raise CallPathError(scrub(f"adding the key for {account} failed: {exc}")) from exc
    problems, answers = False, {}
    for account, methods, allowance in plan(cfg):
        try:
            answers[account] = fetch(account, cfg.network)
            lines, bad = describe(answers[account], account, cfg.vault, methods, allowance)
        except ChainError as exc:
            raise CallPathError(f"keys were sent but {account} could not be read back: {exc}") from exc
        for line in lines:
            out(line)
        problems = problems or bad
    if aside:
        for line in move_aside(cfg, answers, home) + link_account_file(cfg, answers, home):
            out(line)
    if problems:
        out("A key is full access or not as planned. On testnet the creation key stays on the chain; use --aside to move its file off this machine.")
        return 0
    out("OK: only the planned function-call keys exist on both accounts.")
    return 0
