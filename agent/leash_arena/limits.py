"""Message checks and per-visitor, global and in-flight limits. Raw network addresses are never kept."""

from __future__ import annotations

import hashlib
import threading
import time
import unicodedata
from collections import deque
from typing import Callable

MAX_MESSAGE_CHARS = 500
HOUR = 3600.0
DAY = 86400.0
MIN_SALT_BYTES = 16
SWEEP_SECONDS = 1.0


class MessageRejected(Exception):
    """The visitor text cannot be used. `reason` is a short stable word."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def clean_message(raw: object) -> str:
    """Return the visitor text without control characters, or raise MessageRejected."""
    if not isinstance(raw, str):
        raise MessageRejected("not_text")
    if len(raw) > MAX_MESSAGE_CHARS:
        raise MessageRejected("too_long")
    kept = "".join(
        ch for ch in raw
        if ch in "\n\t" or not (unicodedata.category(ch).startswith("C") or unicodedata.category(ch) in ("Zl", "Zp"))
    ).strip()
    if not kept:
        raise MessageRejected("empty")
    return kept


def visitor_key(client: str, salt: bytes) -> str:
    """A salted hash of the client address; the address itself is not stored anywhere."""
    if not isinstance(salt, bytes) or len(salt) < MIN_SALT_BYTES:
        raise ValueError(f"the salt must be at least {MIN_SALT_BYTES} bytes")
    return hashlib.sha256(salt + b"|" + str(client).encode()).hexdigest()[:16]


class Limiter:
    """Admits an attempt or names the reason it was refused. Refusals use up no quota."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        per_hour: int = 10,
        per_day: int = 30,
        global_per_hour: int = 300,
        max_active: int = 10,
        max_visitors: int = 20000,
    ) -> None:
        self.clock = clock
        self.per_hour, self.per_day = per_hour, per_day
        self.global_per_hour, self.max_active, self.max_visitors = global_per_hour, max_active, max_visitors
        self._hits: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._in_flight: set[str] = set()
        self._active = 0
        self._last_sweep = 0.0
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - DAY]:
            del self._hits[key]
        self._last_sweep = now

    def admit(self, visitor: str) -> str | None:
        """None when admitted. Otherwise one of: in_flight, busy, visitor_hour, visitor_day, global_hour."""
        with self._lock:
            now = self.clock()
            if visitor in self._in_flight:
                return "in_flight"
            if self._active >= self.max_active:
                return "busy"
            hits = self._hits.get(visitor)
            if hits is None and len(self._hits) >= self.max_visitors:
                if now - self._last_sweep >= SWEEP_SECONDS:
                    self._sweep(now)
                if len(self._hits) >= self.max_visitors:
                    return "busy"
            hits = self._hits.setdefault(visitor, deque())
            while hits and hits[0] <= now - DAY:
                hits.popleft()
            if sum(1 for t in hits if t > now - HOUR) >= self.per_hour:
                return "visitor_hour"
            if len(hits) >= self.per_day:
                return "visitor_day"
            while self._global and self._global[0] <= now - HOUR:
                self._global.popleft()
            if len(self._global) >= self.global_per_hour:
                return "global_hour"
            hits.append(now)
            self._global.append(now)
            self._in_flight.add(visitor)
            self._active += 1
            return None

    def release(self, visitor: str) -> None:
        with self._lock:
            if visitor in self._in_flight:
                self._in_flight.discard(visitor)
                self._active = max(0, self._active - 1)
