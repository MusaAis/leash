"""The model spending cap. It is checked before every model call and fails closed."""

from __future__ import annotations

import json
import os
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

MICRO = 1_000_000


class BudgetExhausted(Exception):
    """No model call may be made: the cap is reached or the budget file cannot be trusted."""


def _micro(value: str, what: str) -> int:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{what} must be a decimal number") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{what} must be positive")
    return int((amount * MICRO).to_integral_value(rounding="ROUND_CEILING"))


class ModelBudget:
    """Spent money in millionths of a dollar, kept in a file with mode 0600.

    `price_per_million_usd` is the dearest token price of the model, so costs are never understated.
    """

    def __init__(self, path: Path, cap_usd: str, price_per_million_usd: str) -> None:
        self.path = Path(path)
        self.cap_micro = _micro(cap_usd, "the cap")
        self.price_micro = _micro(price_per_million_usd, "the price")
        self._held = 0
        self._lock = threading.Lock()
        self._broken: str | None = None
        self._spent = 0
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            raw = None
        except OSError:
            self._broken = "the budget file cannot be read"
            raw = None
        if raw is not None:
            try:
                data = json.loads(raw)
                spent = data["spent_micro"]
                if not isinstance(spent, int) or isinstance(spent, bool) or spent < 0:
                    raise ValueError("bad field")
                self._spent = spent
            except (ValueError, KeyError, TypeError):
                self._broken = "the budget file is damaged"

    def cost_micro(self, tokens: int) -> int:
        """Cost of `tokens`, rounded up."""
        return -(-max(0, int(tokens)) * self.price_micro // MICRO)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"spent_micro": self._spent}, handle)
        os.replace(tmp, self.path)

    def reserve(self, worst_tokens: int) -> int:
        """Hold the worst-case cost of one call. Raises BudgetExhausted when it does not fit."""
        with self._lock:
            if self._broken:
                raise BudgetExhausted(self._broken)
            worst = self.cost_micro(worst_tokens)
            if self._spent + self._held + worst > self.cap_micro:
                raise BudgetExhausted("the model budget is used up")
            self._held += worst
            return worst

    def settle(self, reservation: int, actual_tokens: int) -> int:
        """Release the hold and record what the call cost. Returns that cost."""
        with self._lock:
            self._held = max(0, self._held - reservation)
            cost = self.cost_micro(actual_tokens)
            self._spent += cost
            try:
                self._write()
            except OSError:
                self._broken = "the budget file cannot be written"
            return cost

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return bool(self._broken) or self._spent >= self.cap_micro

    def snapshot(self) -> dict:
        with self._lock:
            return {"spent_micro_usd": self._spent, "cap_micro_usd": self.cap_micro,
                    "exhausted": bool(self._broken) or self._spent >= self.cap_micro}
