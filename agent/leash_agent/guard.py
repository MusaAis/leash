"""Shared network guard (LEAD, 2026-11-01). Every command that names accounts passes them through `check`."""
from __future__ import annotations

import os
import re
from typing import Iterable, Mapping

from .chain import is_valid_account_id

IMPLICIT = re.compile(r"[0-9a-f]{64}")


class GuardError(Exception):
    pass


def check(network: str, accounts: Iterable[str], env: Mapping[str, str] | None = None) -> list[str]:
    """Return the accounts if they fit the network, else raise GuardError (never prints a key or a value from .env)."""
    env = os.environ if env is None else env
    accounts = list(accounts)
    if network == "mainnet":
        if env.get("LEASH_ALLOW_MAINNET") != "1":
            raise GuardError("mainnet needs LEASH_ALLOW_MAINNET=1 in the process environment, set for this one command")
    elif network != "testnet":
        raise GuardError(f"unknown network {network!r}: only testnet and mainnet are known")
    if not accounts:
        raise GuardError("no account given: the guard checks every account in the command")
    for account in accounts:
        if not is_valid_account_id(account):
            raise GuardError(f"{account!r} is not a valid NEAR account id")
        if network == "mainnet" and account.endswith(".testnet"):
            raise GuardError(f"{account} is a testnet account, but the network is mainnet")
        if network == "testnet" and not account.endswith(".testnet") and not IMPLICIT.fullmatch(account):
            raise GuardError(f"{account} is not a .testnet or implicit account, but the network is testnet")
    return accounts
