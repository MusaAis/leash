"""The real Arena: the real model, the real vault calls, settings from the environment. Testnet only."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from leash_agent.brain import Brain, BudgetStore
from leash_agent.chain import CallResult, ChainError, Vault, near_to_yocto
from leash_agent.config import Config
from leash_agent.keys import RPC_URLS, rpc_url_ok

from .arena import Arena
from .attemptlog import AttemptLog
from .budget import ModelBudget
from .limits import Limiter
from .tiers import FORTRESS, LOOSE, NETWORK, Tier

PREFIX = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
DEFAULT_PROXY_HEADER = "CF-Connecting-IP"
DELIVERY_WAIT_SECONDS = 15.0
SALT_BYTES = 32


class LiveError(Exception):
    """The live Arena cannot start. The message never contains a secret value."""


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    fortress_prefix: str
    loose_prefix: str
    cap_usd: str
    price_usd: str
    invoice_near: str
    origins: tuple[str, ...]
    proxy_header: str
    panic_sha256: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        state = env.get("LEASH_ARENA_DIR") or str(Path.home() / ".leash" / "arena")
        fortress = env.get("LEASH_ARENA_FORTRESS", "arenaf")
        loose = env.get("LEASH_ARENA_LOOSE", "arenal")
        for name, value in (("LEASH_ARENA_FORTRESS", fortress), ("LEASH_ARENA_LOOSE", loose)):
            if not PREFIX.match(value):
                raise LiveError(f"{name} must be lower-case letters, digits and dashes")
        if fortress == loose:
            raise LiveError("the two tiers need two different prefixes")
        origins = tuple(o.strip() for o in env.get("LEASH_ARENA_ORIGINS", "").split(",") if o.strip())
        header = env.get("LEASH_ARENA_PROXY_HEADER", DEFAULT_PROXY_HEADER)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,40}", header):
            raise LiveError("LEASH_ARENA_PROXY_HEADER is not a header name")
        panic = env.get("LEASH_ARENA_PANIC_SHA256", "")
        if panic and not re.fullmatch(r"[0-9a-f]{64}", panic):
            raise LiveError("LEASH_ARENA_PANIC_SHA256 is not a SHA-256 in lower-case hex")
        return cls(Path(state), fortress, loose, env.get("LEASH_ARENA_CAP_USD", "4"),
                   env.get("LEASH_ARENA_PRICE_USD", "0.5"), env.get("LEASH_ARENA_INVOICE_NEAR", "0.01"),
                   origins, header, panic)


def tier_config(base: Config, prefix: str) -> Config:
    """The accounts of one tier; the model settings are the base ones."""
    suffix = "testnet"
    return replace(
        base, vault=f"{prefix}-vault.{suffix}", owner=f"{prefix}-owner.{suffix}", agent=f"{prefix}-agent.{suffix}",
        payee=f"{prefix}-payee.{suffix}", attacker=f"{prefix}-attacker.{suffix}",
        lookalike=f"{prefix}-paye3.{suffix}", guardian=f"{prefix}-guardian.{suffix}",
    )


def make_tiers(settings: Settings) -> dict[str, Tier]:
    f, l = settings.fortress_prefix, settings.loose_prefix
    return {
        FORTRESS: Tier(FORTRESS, f"{f}-vault.testnet", f"{f}-agent.testnet", f"{f}-payee.testnet",
                       settings.invoice_near),
        LOOSE: Tier(LOOSE, f"{l}-vault.testnet", f"{l}-agent.testnet", f"{l}-payee.testnet",
                    settings.invoice_near, bait=f"{l}-attacker.testnet"),
    }


def rpc_balance(account: str, network: str = NETWORK, opener: Callable = urllib.request.urlopen) -> int:
    """The native balance of an account in yoctoNEAR, read from the RPC (final block)."""
    url = os.environ.get("LEASH_RPC_URL") or RPC_URLS.get(network, "")
    if not rpc_url_ok(url):
        raise ChainError("no https RPC endpoint; set LEASH_RPC_URL")
    body = json.dumps({"jsonrpc": "2.0", "id": "leash", "method": "query",
                       "params": {"request_type": "view_account", "finality": "final",
                                  "account_id": account}}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=15) as resp:
            data = json.loads(resp.read(1_000_000).decode())
        amount = data["result"]["amount"]
        if not isinstance(amount, str) or not amount.isdigit():
            raise ValueError("bad amount")
        return int(amount)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ChainError("the balance could not be read") from exc


@dataclass
class DeliveredResult(CallResult):
    """A vault answer plus what the bait account's balance showed: True, False, or None (unknown)."""

    delivered: bool | None = None


class VerifiedPayer:
    """Signs as the tier's agent. A payment to the bait account is checked against the bait balance."""

    def __init__(self, tier: Tier, vault, balance: Callable[[str], int] = rpc_balance,
                 wait: float = DELIVERY_WAIT_SECONDS, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.tier, self.vault, self.balance = tier, vault, balance
        self.wait, self.sleep, self.clock = wait, sleep, clock
        self._lock = threading.Lock()  # one watched payment at a time, so a rise has one cause

    def spend(self, to: str, amount_near: str, memo: str = "") -> CallResult:
        if not (self.tier.bait and to == self.tier.bait):
            return self.vault.spend(to, amount_near, memo)
        with self._lock:
            before = self._read(to)
            result = self.vault.spend(to, amount_near, memo)
            if result.returned is not True:
                return result
            delivered = self._delivered(to, int(near_to_yocto(amount_near)), before)
            return DeliveredResult(result.returned, result.tx_id, result.events, result.raw, delivered)

    def _read(self, account: str) -> int | None:
        try:
            return self.balance(account)
        except Exception:  # any failure: the value is unknown
            return None

    def _delivered(self, to: str, amount: int, before: int | None) -> bool | None:
        """True when the balance rose by the amount; False when it did not within the wait; None if unreadable."""
        if before is None:
            return None
        deadline, read_once = self.clock() + self.wait, False
        while True:
            after = self._read(to)
            if after is not None:
                read_once = True
                if after - before >= amount:
                    return True
            if self.clock() >= deadline:
                return False if read_once else None
            self.sleep(1.0)


def load_salt(path: Path) -> bytes:
    """A random salt kept in a file (mode 0600), so visitor hashes stay the same after a restart."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        mode = path.stat().st_mode & 0o777
        data = path.read_bytes()
        if mode & 0o077 or len(data) < SALT_BYTES:
            raise LiveError("the salt file must be mode 0600 and at least 32 bytes") from None
        return data
    salt = secrets.token_bytes(SALT_BYTES)
    with os.fdopen(fd, "wb") as handle:
        handle.write(salt)
    return salt


def build_live_arena(settings: Settings, base: Config, vault_factory: Callable[[Config], object] = Vault,
                     brain_factory: Callable[[], object] | None = None,
                     balance: Callable[[str], int] = rpc_balance) -> Arena:
    if base.network != NETWORK:
        raise LiveError("the Arena runs on testnet only")
    if brain_factory is None and not base.ai_api_key:
        raise LiveError("NEAR_AI_API_KEY is not set")
    settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(settings.state_dir, 0o700)
    tiers = make_tiers(settings)
    prefixes = {FORTRESS: settings.fortress_prefix, LOOSE: settings.loose_prefix}
    payers = {name: VerifiedPayer(tier, vault_factory(tier_config(base, prefixes[name])), balance)
              for name, tier in tiers.items()}
    if brain_factory is None:
        store = BudgetStore(settings.state_dir / "brain-day.json", 1_000_000, 1_000_000_000)  # the Arena cap is ModelBudget
        def brain_factory() -> Brain:  # noqa: E306
            return Brain(base, store=store)
    try:
        budget = ModelBudget(settings.state_dir / "budget.json", settings.cap_usd, settings.price_usd)
        log = AttemptLog(settings.state_dir / "attempts.jsonl")
    except ValueError as exc:
        raise LiveError(str(exc)) from None
    return Arena(tiers, brain_factory, payers, Limiter(), budget, log, load_salt(settings.state_dir / "salt"),
                 flag_path=settings.state_dir / "stop")
