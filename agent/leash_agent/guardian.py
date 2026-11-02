"""The guardian: a route id and an amount in, a checked 1Click quote out. Moves no funds."""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .chain import Snapshot
from .intents import (
    DEFAULT_DEADLINE_MINUTES, DEFAULT_SLIPPAGE_BPS, TERMINAL, IntentsError, OneClick, QuoteRejected,
    QuoteRequest, VerifiedQuote, build_quote_request, parse_iso, utc_now, verify_quote_response,
)

DEFAULT_MAX_PER_HOUR = 6
MAX_CALLS_PER_MINUTE, MAX_CALLS_PER_HOUR = 10, 60  # every call to 1Click, whatever its result
MAX_ROUTE_ID = 9  # the contract allows 10 routes per agent


class Refused(IntentsError):
    """The guardian refused before asking 1Click: the vault would deny, or a guardian limit applies."""


@dataclass(frozen=True)
class Route:
    dest_asset: str
    recipient: str


class RouteSource(Protocol):
    def get(self, agent: str, route_id: int) -> Route: ...


class EnvRoutes:
    """One route (id 0) from the environment. The recipient lives only in `.env`."""

    def __init__(self, dest_asset: str, recipient: str) -> None:
        self._route = Route(dest_asset, recipient)

    @classmethod
    def from_env(cls, dest_asset: str | None = None) -> "EnvRoutes":
        asset = dest_asset or os.environ.get("LEASH_ROUTE_DEST_ASSET", "")
        recipient = os.environ.get("LEASH_ROUTE_RECIPIENT", "")
        if not asset or not recipient:
            raise IntentsError("set LEASH_ROUTE_DEST_ASSET and LEASH_ROUTE_RECIPIENT in .env (see .env.example)")
        return cls(asset, recipient)

    def get(self, agent: str, route_id: int) -> Route:
        if route_id != 0:
            raise Refused(f"unknown route {route_id}")
        return self._route


# Two wNEAR storage deposits per swap (0.00125 NEAR each); same value as the contract's INTENT_OVERHEAD.
STORAGE_DEPOSIT = 1_250_000_000_000_000_000_000
INTENT_OVERHEAD = 2 * STORAGE_DEPOSIT


def intent_denial(snap: Snapshot, amount: int) -> str | None:
    """The reason the vault would deny `spend_intent`, in the contract's order, or None. (Allowlist: not used.)"""
    if snap.all_paused:
        return "all_paused"
    if snap.agent_paused:
        return "agent_paused"
    if amount <= 0:
        return "zero_amount"
    if amount > snap.per_tx_cap:
        return "over_per_tx_cap"
    if snap.spent + amount + INTENT_OVERHEAD > snap.daily_cap:
        return "over_daily_cap"
    if amount + INTENT_OVERHEAD > snap.balance:  # `balance` is the vault's available balance, already net of reserve
        return "insufficient_funds"
    return None


class GuardianLog:
    """Append-only JSON lines, private (mode 0600). Holds quote signatures, so it is never committed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def from_env(cls) -> "GuardianLog":
        return cls(Path(os.environ.get("LEASH_GUARDIAN_LOG") or Path.home() / ".leash" / "guardian-log.jsonl"))

    def _secure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent == Path.home() / ".leash":
            os.chmod(self.path.parent, 0o700)

    def append(self, entry: dict) -> None:
        self._secure_dir()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)  # an older, wider file is tightened too
        with os.fdopen(fd, "a") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    @contextlib.contextmanager
    def lock(self):
        """One process at a time between a limit check and the entry it protects."""
        self._secure_dir()
        fd = os.open(f"{self.path}.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def entries(self) -> list[dict]:
        try:
            lines = self.path.read_text().splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise IntentsError(f"guardian log {self.path} cannot be read: {exc}") from exc
        out = []
        for number, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("not an object")
            except ValueError as exc:  # fail closed: a damaged log could hide the limits
                raise IntentsError(f"guardian log {self.path} is damaged at line {number}; fix or move it") from exc
            out.append(item)
        return out


@dataclass(frozen=True)
class Issued:
    request: QuoteRequest
    verified: VerifiedQuote
    response: dict


class Guardian:
    def __init__(self, *, agent: str, vault_account: str, snapshot: Callable[[], Snapshot], routes: RouteSource,
                 oneclick: OneClick, log: GuardianLog, other_accounts: tuple[str, ...] = (),
                 now: Callable[[], datetime.datetime] = utc_now, max_per_hour: int = DEFAULT_MAX_PER_HOUR,
                 slippage_bps: int = DEFAULT_SLIPPAGE_BPS, deadline_minutes: int = DEFAULT_DEADLINE_MINUTES,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.agent, self.vault_account, self._snapshot, self.routes = agent, vault_account, snapshot, routes
        self.oneclick, self.log, self.other_accounts, self._now = oneclick, log, other_accounts, now
        self.max_per_hour, self.slippage_bps, self.deadline_minutes, self._sleep = (
            max_per_hour, slippage_bps, deadline_minutes, sleep)

    # inputs from the agent
    def _inputs(self, route_id: object, amount: object) -> tuple[int, int, Route]:
        if isinstance(route_id, bool) or not isinstance(route_id, int) or not 0 <= route_id <= MAX_ROUTE_ID:
            raise Refused("unknown_route")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise Refused("zero_amount")
        return route_id, amount, self.routes.get(self.agent, route_id)

    def _forbidden(self) -> tuple[str, ...]:
        return (self.vault_account, self.agent, *self.other_accounts)

    # quotes
    def dry_quote(self, route_id: int, amount: int, refund_to: str | None = None) -> Issued:
        """A quote with `dry: true`: no deposit address, nothing to approve, no funds. Reads no vault state."""
        route_id, amount, route = self._inputs(route_id, amount)
        req = build_quote_request(
            dest_asset=route.dest_asset, recipient=route.recipient, refund_to=refund_to or self.vault_account,
            amount=amount, dry=True, now=self._now(), deadline_minutes=self.deadline_minutes, slippage_bps=self.slippage_bps)
        with self.log.lock():
            return self._ask(req)

    def quote(self, route_id: int, amount: int) -> Issued:
        """A real quote (`dry: false`): refuse early, ask 1Click, verify, log. One call per approval attempt."""
        route_id, amount, route = self._inputs(route_id, amount)
        reason = intent_denial(self._snapshot(), amount)
        if reason:
            self.log.append({"event": "refused", "ts": self._now().isoformat(), "agent": self.agent,
                             "route_id": route_id, "amount": str(amount), "reason": reason})
            raise Refused(reason)
        req = build_quote_request(
            dest_asset=route.dest_asset, recipient=route.recipient, refund_to=self.vault_account,
            amount=amount, dry=False, now=self._now(), deadline_minutes=self.deadline_minutes, slippage_bps=self.slippage_bps)
        with self.log.lock():
            self._limits()
            issued = self._ask(req)
            v = issued.verified
            self.log.append({
                "event": "quote_issued", "ts": self._now().isoformat(), "agent": self.agent, "route_id": route_id,
                "amount": str(amount), "deposit_address": v.deposit_address,
                "deadline": v.deadline.isoformat() if v.deadline else None, "request_deadline": req.body()["deadline"],
                "echoed_deadline": issued.response.get("quoteRequest", {}).get("deadline"),
                "signature": v.signature, "response": issued.response,
            })
            return issued

    def _call_limit(self, entries: list[dict]) -> None:
        now = self._now()
        times = []
        for entry in entries:
            if entry.get("event") == "quote_call":
                try:
                    times.append(datetime.datetime.fromisoformat(str(entry["ts"])))
                except (KeyError, ValueError) as exc:
                    raise IntentsError("guardian log has an entry without a valid time; fix or move it") from exc
        minute = sum(1 for t in times if now - t < datetime.timedelta(minutes=1))
        hour = sum(1 for t in times if now - t < datetime.timedelta(hours=1))
        if minute >= MAX_CALLS_PER_MINUTE or hour >= MAX_CALLS_PER_HOUR:
            raise Refused(f"rate_limit: {minute} calls to 1Click in the last minute, {hour} in the last hour")

    def _ask(self, req: QuoteRequest) -> Issued:
        self._call_limit(self.log.entries())
        self.log.append({"event": "quote_call", "ts": self._now().isoformat(), "agent": self.agent, "dry": req.dry})
        try:
            response = self.oneclick.quote(req)
        except IntentsError as exc:
            self.log.append({"event": "quote_failed", "ts": self._now().isoformat(), "agent": self.agent, "reason": str(exc)})
            raise
        try:
            verified = verify_quote_response(req, response, now=self._now(), forbidden_deposit=self._forbidden())
        except QuoteRejected as exc:
            self.log.append({"event": "quote_rejected", "ts": self._now().isoformat(), "agent": self.agent,
                             "reason": str(exc), "dry": req.dry, "response": response})
            exc.response = response
            raise
        return Issued(req, verified, response if isinstance(response, dict) else {})

    def _limits(self) -> None:
        """At most N real quotes per hour, and one outstanding quote at a time (both counted from the log)."""
        now, entries = self._now(), self.log.entries()
        issued = [e for e in entries if e.get("event") == "quote_issued" and e.get("agent") == self.agent]
        recent = 0
        for entry in issued:
            try:
                when = datetime.datetime.fromisoformat(str(entry["ts"]))
            except (KeyError, ValueError) as exc:
                raise IntentsError("guardian log has an entry without a valid time; fix or move it") from exc
            if now - when < datetime.timedelta(hours=1):
                recent += 1
        if recent >= self.max_per_hour:
            raise Refused(f"rate_limit: {recent} quotes in the last hour (limit {self.max_per_hour})")
        finished = {e.get("deposit_address") for e in entries if e.get("event") == "final"}
        for entry in issued:
            try:
                deadline = parse_iso(entry.get("request_deadline") or entry["deadline"])
            except (KeyError, ValueError, TypeError) as exc:
                raise IntentsError("guardian log has a quote without a valid deadline; fix or move it") from exc
            if entry.get("deposit_address") not in finished and deadline > now:
                raise Refused("outstanding_quote: the previous quote is still open (wait for its final status or its deadline)")

    # status
    def watch(self, deposit_address: str, *, max_wait_s: int = 1800, interval_s: int = 15) -> dict:
        """Poll 1Click until a final status (SUCCESS, REFUNDED, FAILED), then record it. Gives up after max_wait_s."""
        waited, last = 0, {}
        while True:
            last = self.oneclick.status(deposit_address)
            if last["status"] in TERMINAL:
                self.log.append({"event": "final", "ts": self._now().isoformat(), "agent": self.agent,
                                 "deposit_address": deposit_address, "status": last["status"], "response": last})
                return last
            if waited >= max_wait_s:
                raise IntentsError(f"no final status after {max_wait_s} s; last status {last['status']}")
            self._sleep(interval_s)
            waited += interval_s

