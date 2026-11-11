"""The dashboard: read-only vault views over the NEAR RPC and the panic switch. Standard library only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Mapping

from leash_agent.chain import ChainError, run_near
from leash_agent.config import Config
from leash_agent.keys import RPC_URLS, rpc_url_ok

NETWORK = "testnet"
CACHE_SECONDS = 15.0
MAX_FAILURES, FAILURE_WINDOW = 5, 60.0  # wrong tokens per minute before the switch locks


def near_text(yocto: int) -> str:
    whole, frac = divmod(int(yocto), 10**24)
    return f"{whole}.{frac // 10**20:04d}"


def rpc_view(account: str, method: str, args: dict | None = None, opener: Callable = urllib.request.urlopen) -> object:
    """One view call on a contract (final block); the answer is the parsed JSON value."""
    url = os.environ.get("LEASH_RPC_URL") or RPC_URLS.get(NETWORK, "")
    if not rpc_url_ok(url):
        raise ChainError("no https RPC endpoint; set LEASH_RPC_URL")
    encoded = base64.b64encode(json.dumps(args or {}).encode()).decode()
    body = json.dumps({"jsonrpc": "2.0", "id": "leash", "method": "query",
                       "params": {"request_type": "call_function", "finality": "final",
                                  "account_id": account, "method_name": method, "args_base64": encoded}}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=15) as resp:
            data = json.loads(resp.read(1_000_000).decode())
        return json.loads(bytes(data["result"]["result"]).decode())
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ChainError("a view could not be read") from exc


def _safe(call: Callable[[], object]) -> object | None:
    try:
        return call()
    except ChainError:
        return None


def vault_snapshot(cfg: Config, opener: Callable = urllib.request.urlopen) -> dict:
    """The public state of one vault. A view that cannot be read is null, never a guess."""
    def view(method: str, args: dict | None = None) -> object | None:
        return _safe(lambda: rpc_view(cfg.vault, method, args, opener))

    policy, balance = view("get_policy", {"agent": cfg.agent}), view("get_available_balance")
    stats, paused_all, head = view("get_stats"), view("is_paused_all"), view("get_head_hash")
    out: dict = {"vault": cfg.vault, "agent": cfg.agent, "policy": None, "available_near": None,
                 "allowed": None, "denied": None, "paused_all": None, "head_hash": None}
    try:
        if isinstance(policy, dict):
            out["policy"] = {"per_tx_cap_near": near_text(int(policy["per_tx_cap"])),
                             "daily_cap_near": near_text(int(policy["daily_cap"])),
                             "spent_in_window_near": near_text(int(policy["spent_in_window"])),
                             "paused": bool(policy["paused"]), "allowlist": [str(a) for a in policy["allowlist"]]}
        if isinstance(balance, str) and balance.isdigit():
            out["available_near"] = near_text(int(balance))
        if isinstance(stats, list) and len(stats) == 2:
            out["allowed"], out["denied"] = int(stats[0]), int(stats[1])
        if isinstance(paused_all, bool):
            out["paused_all"] = paused_all
        if isinstance(head, str) and len(head) == 64:
            out["head_hash"] = head
    except (KeyError, TypeError, ValueError):
        pass  # a view in an unexpected shape stays null
    return out


def pause_all_with_near(cfg: Config) -> None:
    """Pause one vault through the near CLI, signed by the tier's owner account."""
    run_near(["near", "contract", "call-function", "as-transaction", cfg.vault, "pause_all", "json-args", "{}",
              "prepaid-gas", "30 Tgas", "attached-deposit", "0 NEAR", "sign-as", cfg.owner,
              "network-config", cfg.network, "sign-with-legacy-keychain", "send"])


class Dashboard:
    """Cached vault snapshots and the panic switch behind a token whose SHA-256 is the only thing stored."""

    def __init__(self, configs: Mapping[str, Config], flag_path: Path, token_sha256: str | None = None,
                 opener: Callable = urllib.request.urlopen, pauser: Callable[[Config], None] = pause_all_with_near,
                 clock: Callable[[], float] = time.monotonic, ttl: float = CACHE_SECONDS) -> None:
        self.configs, self.flag_path, self.token_sha256 = dict(configs), flag_path, token_sha256
        self.opener, self.pauser, self.clock, self.ttl = opener, pauser, clock, ttl
        self._lock, self._cached, self._at = threading.Lock(), None, 0.0
        self._failures: deque[float] = deque()

    @property
    def panic_enabled(self) -> bool:
        return bool(self.token_sha256)

    def snapshot(self) -> dict:
        with self._lock:
            if self._cached is None or self.clock() - self._at >= self.ttl:
                self._cached = {name: vault_snapshot(cfg, self.opener) for name, cfg in self.configs.items()}
                self._at = self.clock()
            return {"network": NETWORK, "panic_enabled": self.panic_enabled, "tiers": self._cached}

    def locked(self) -> bool:
        now = self.clock()
        while self._failures and now - self._failures[0] > FAILURE_WINDOW:
            self._failures.popleft()
        return len(self._failures) >= MAX_FAILURES

    def token_ok(self, presented: str) -> bool:
        """True for the right token. Wrong tokens are counted; five a minute lock the switch."""
        if not self.token_sha256 or self.locked():
            return False
        digest = hashlib.sha256(presented.encode("utf-8", "replace")).hexdigest()
        if hmac.compare_digest(digest, self.token_sha256):
            return True
        self._failures.append(self.clock())
        return False

    def panic(self) -> dict:
        """Stop new attempts first (the stop file), then pause every vault. The stop file is kept even if a pause fails."""
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.flag_path.touch(mode=0o600)

        def one(item: tuple[str, Config]) -> tuple[str, str]:
            try:
                self.pauser(item[1])
                return item[0], "paused"
            except ChainError:
                return item[0], "failed"

        with ThreadPoolExecutor(max_workers=max(1, len(self.configs))) as pool:
            results = dict(pool.map(one, list(self.configs.items())))
        with self._lock:
            self._cached = None  # the next read shows the new state
        return {"stopped": True, "tiers": results}
