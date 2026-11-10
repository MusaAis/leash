"""One attempt from start to end: checks, one model call, one vault call, one log line."""

from __future__ import annotations

import datetime
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from leash_agent.agent import Decider, Payer
from leash_agent.chain import clean_text
from leash_agent.redact import scrub

from .attemptlog import AttemptLog, public_record
from .budget import BudgetExhausted, ModelBudget
from .counters import Counters
from .limits import Limiter, MessageRejected, clean_message, visitor_key
from .tiers import Tier, funds_lost_yocto, is_fooled, owner_task

RECENT_ATTEMPTS = 200


@dataclass
class AttemptResult:
    status: str  # "done" or "rejected"
    reason: str | None = None  # why it was rejected
    record: dict | None = None  # the full record of a finished attempt

    @property
    def public(self) -> dict:
        """What the page may show: a record without private fields, or the rejection reason."""
        if self.record is not None:
            return {"status": self.status, **public_record(self.record)}
        return {"status": self.status, "reason": self.reason}


def _rejected(reason: str) -> AttemptResult:
    return AttemptResult("rejected", reason=reason)


class Arena:
    """Runs attempts against the tiers. The brain and the payers are injected, so tests use fakes."""

    def __init__(
        self,
        tiers: dict[str, Tier],
        brain_factory: Callable[[], Decider],
        payers: dict[str, Payer],
        limiter: Limiter,
        budget: ModelBudget,
        log: AttemptLog,
        salt: bytes,
        flag_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        max_output_tokens: int = 4000,
        max_task_chars: int = 3000,
        concurrency: int = 2,
        wait_seconds: float = 90.0,
    ) -> None:
        if set(tiers) != set(payers):
            raise ValueError("every tier needs exactly one payer")
        self.tiers, self.brain_factory, self.payers = tiers, brain_factory, payers
        self.limiter, self.budget, self.log, self.salt = limiter, budget, log, salt
        self.flag_path, self.clock = Path(flag_path) if flag_path else None, clock
        self.max_output_tokens, self.max_task_chars = max_output_tokens, max_task_chars
        self.wait_seconds = wait_seconds
        self._slots = threading.Semaphore(concurrency)
        self._lock = threading.Lock()
        self._log_failed = False
        self.counters = Counters(tuple(tiers))
        self._recent: deque[dict] = deque(maxlen=RECENT_ATTEMPTS)
        self._last_id = 0
        for record in log.replay():
            if self.counters.apply(record):
                self._recent.append(public_record(record))
            rid = record.get("id")
            if isinstance(rid, int) and not isinstance(rid, bool):
                self._last_id = max(self._last_id, rid)

    def _flag_set(self) -> bool:
        return self.flag_path is not None and self.flag_path.exists()

    def attempt(self, tier_name: str, client: str, message: object) -> AttemptResult:
        tier = self.tiers.get(tier_name)
        if tier is None:
            return _rejected("unknown_tier")
        if self._flag_set():
            return _rejected("paused")
        if self._log_failed:
            return _rejected("stopped")
        try:
            text = clean_message(message)
        except MessageRejected as exc:
            return _rejected(exc.reason)
        task = owner_task(tier, text)
        if len(task) > self.max_task_chars:
            return _rejected("too_long")
        visitor = visitor_key(client, self.salt)
        reason = self.limiter.admit(visitor)
        if reason:
            return _rejected(reason)
        try:
            return self._run(tier, visitor, text, task)
        finally:
            self.limiter.release(visitor)

    def _run(self, tier: Tier, visitor: str, text: str, task: str) -> AttemptResult:
        worst = self.max_output_tokens + len(task)
        try:
            hold = self.budget.reserve(worst)
        except BudgetExhausted:
            return _rejected("replay")
        if not self._slots.acquire(timeout=self.wait_seconds):
            self.budget.settle(hold, 0)
            return _rejected("busy")
        decision, error, tokens = None, None, 0
        try:
            brain = None
            try:
                brain = self.brain_factory()
                decision = brain.decide(task)
            except Exception as exc:  # the model call failed in any way
                error = ("model_error", clean_text(scrub(str(exc)), 200))
            used = getattr(brain, "tokens_used", None)
            if isinstance(used, int) and not isinstance(used, bool) and used >= 0:
                tokens = used
            elif decision is not None:
                tokens = worst
        finally:
            self._slots.release()
        cost = self.budget.settle(hold, tokens)

        record: dict = {"tier": tier.name, "visitor": visitor, "message": text,
                        "tokens": tokens, "cost_micro_usd": cost}
        if error or decision is None:
            kind, detail = error or ("model_error", "no decision")
            record.update(outcome=kind, detail=detail, fooled=False, funds_lost=False)
        else:
            record.update(to=decision.to, amount_near=decision.amount_near, memo=decision.memo,
                          reason=decision.reason, fooled=is_fooled(tier, decision))
            record.update(self._vault_step(tier, decision))
        return self._finish(record)

    def _vault_step(self, tier: Tier, decision) -> dict:
        if decision.action != "spend":
            return {"outcome": "refused", "funds_lost": False}
        if self._flag_set():
            return {"outcome": "stopped", "funds_lost": False}
        try:
            result = self.payers[tier.name].spend(decision.to, decision.amount_near, decision.memo)
        except Exception as exc:  # the vault call failed in any way
            return {"outcome": "chain_error", "funds_lost": False,
                    "detail": clean_text(scrub(str(exc)), 200)}
        lost = funds_lost_yocto(tier, decision, result)
        step = {"tx_id": result.tx_id, "funds_lost": lost > 0}
        if result.returned is True and getattr(result, "delivered", None) is False:
            step["outcome"] = "transfer_failed"  # allowed by the vault, but the money did not arrive
        elif result.returned is True:
            step["outcome"] = "paid_other" if decision.to != tier.payee else "paid_payee"
            if lost:
                step["funds_lost_yocto"] = str(lost)
        else:
            step.update(outcome="blocked", denial_reason=result.denial_reason)
        return step

    def _finish(self, record: dict) -> AttemptResult:
        with self._lock:
            self._last_id += 1
            record["id"] = self._last_id
            record["ts"] = datetime.datetime.fromtimestamp(
                self.clock(), datetime.timezone.utc).isoformat(timespec="seconds")
            self.counters.apply(record)
            self._recent.append(public_record(record))
            try:
                self.log.append(record)
            except OSError:
                self._log_failed = True  # without a log line no new attempt starts
        return AttemptResult("done", record=record)

    def stats(self) -> dict:
        """Both numbers per tier and in total, the network, and the model budget."""
        return {**self.counters.snapshot(), "budget": self.budget.snapshot(),
                "paused": self._flag_set(), "replay": self.budget.exhausted}

    def feed(self, limit: int = 50) -> list[dict]:
        """The newest public records first."""
        with self._lock:
            return list(self._recent)[::-1][: max(0, limit)]
