"""The pause key: a function-call key on the owner account that can call only `pause_all` and `pause_agent`."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from .callpath import CallPathError, guard_or_raise
from .chain import ChainError, run_near
from .config import Config
from .keys import add_key_args, describe, fetch_keys, known_full_access

PANIC_METHODS = ("pause_agent", "pause_all")
PANIC_ALLOWANCE = "0.1"  # NEAR; one 30 Tgas call reserves about 0.031 and the unused part comes back


def has_pause_key(answer: object, vault: str) -> bool:
    """True if the key list holds a function-call key for exactly the pause methods on the vault."""
    try:
        for key in answer["result"]["keys"]:  # type: ignore[index]
            call = key["access_key"]["permission"].get("FunctionCall") if isinstance(key["access_key"]["permission"], dict) else None
            if call and call.get("receiver_id") == vault and sorted(call.get("method_names") or []) == sorted(PANIC_METHODS):
                return True
    except (KeyError, TypeError, AttributeError) as exc:
        raise ChainError(f"unreadable key list: {exc!r}") from exc
    return False


def run(
    cfg: Config, *, confirmed: bool, runner: Callable[[list[str]], str] = run_near, env: Mapping[str, str] | None = None,
    out=print, fetch: Callable[[str, str], object] = fetch_keys, home: Path | None = None,
) -> int:
    """Testnet: plan, and with `confirmed` add the key. Mainnet: read-only check. Then the owner's key list is read back."""
    guard_or_raise(cfg, [cfg.vault, cfg.owner], env)
    if cfg.network == "mainnet" and confirmed:
        raise CallPathError("on mainnet this command only verifies: the pause key is created on the owner's device. Nothing was changed")
    out(f"{cfg.network}: pause key on {cfg.owner}, receiver {cfg.vault}, methods {','.join(PANIC_METHODS)}, allowance {PANIC_ALLOWANCE} NEAR.")
    if cfg.network == "testnet" and confirmed:
        try:
            runner(add_key_args(cfg, cfg.owner, PANIC_METHODS, PANIC_ALLOWANCE))
        except ChainError as exc:
            raise CallPathError(f"adding the pause key failed: {exc}") from exc
    elif cfg.network == "testnet":
        out("plan only: nothing sent. Add --yes to send. The keys now on the account (read-only):")
    try:
        answer = fetch(cfg.owner, cfg.network)
        lines, problems = describe(answer, cfg.owner, cfg.vault, PANIC_METHODS, PANIC_ALLOWANCE, known_full_access(env))
        present = has_pause_key(answer, cfg.vault)
    except ChainError as exc:
        raise CallPathError(f"the key list of {cfg.owner} could not be read: {exc}") from exc
    for line in lines:
        out(line)
    if confirmed and not present:
        raise CallPathError("the pause key was not found on the account after adding it; read the key list again")
    out("The pause key can call: " + ", ".join(PANIC_METHODS) + ". Every other method of the vault needs the owner's full-access key." if present
        else "No pause key is on the account yet.")
    if problems and cfg.network == "mainnet":
        out("A key is full access without being listed, or a function-call key is not as planned.")
        return 1
    return 0
