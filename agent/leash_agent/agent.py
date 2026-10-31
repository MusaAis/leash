"""The agent loop: decide, then ask the vault. The vault has the final say."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .brain import Decision
from .chain import CallResult


class Decider(Protocol):
    def decide(self, task: str) -> Decision: ...


class Payer(Protocol):
    def spend(self, to: str, amount_near: str, memo: str = "") -> CallResult: ...


@dataclass
class Outcome:
    decision: Decision
    result: CallResult | None

    @property
    def paid(self) -> bool:
        return bool(self.result and self.result.returned is True)

    def summary(self) -> str:
        if self.decision.action != "spend":
            return f"refused by the agent: {self.decision.reason}"
        assert self.result is not None
        if self.paid:
            return f"paid {self.decision.amount_near} NEAR to {self.decision.to} (tx {self.result.tx_id})"
        reason = self.result.denial_reason or "unknown"
        return f"blocked by the vault: {reason} (tx {self.result.tx_id})"


class Agent:
    def __init__(self, brain: Decider, vault: Payer, log: Callable[[str], None] = print) -> None:
        self.brain = brain
        self.vault = vault
        self.log = log

    def handle(self, task: str, dry_run: bool = False) -> Outcome:
        decision = self.brain.decide(task)
        self.log(f"decision: {decision}")
        if decision.action != "spend":
            return Outcome(decision, None)
        if dry_run:
            self.log("dry run: not calling the vault")
            return Outcome(decision, None)
        result = self.vault.spend(decision.to, decision.amount_near, decision.memo)
        return Outcome(decision, result)
