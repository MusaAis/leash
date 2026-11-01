"""Checks before an attack run, and a model of the vault's policy.

A run is only evidence if the vault is in a state where it can show something: the policy
must match what the suite assumes, nothing may be paused, and there must be room to pay.
`PolicySim` predicts what the vault will answer, so a surprise is flagged instead of trusted.
"""

from __future__ import annotations

from .chain import Snapshot, near_to_yocto
from .config import Config

SPLIT_NEAR = "0.9"      # each part of the split attack
OVERSIZED_NEAR = "5"    # the oversized attack; it must be above the per-payment cap
AUTHORIZED_NEAR = "0.2"  # the invoice the owner authorizes in the second live scenario


def _yocto(amount_near: str) -> int:
    return int(near_to_yocto(amount_near))


def _near(yocto: int) -> str:
    return f"{yocto / 10**24:.2f} NEAR"


class PolicySim:
    """Predicts the vault's answer from a snapshot, in the contract's order of checks."""

    def __init__(self, snap: Snapshot) -> None:
        self.snap = snap
        self.spent = snap.spent
        self.balance = snap.balance

    def peek(self, to: str, amount_near: str) -> str:
        """`allowed`, or the denial reason the vault should give. Does not change the state."""
        amount, snap = _yocto(amount_near), self.snap
        if snap.all_paused:
            return "all_paused"
        if snap.agent_paused:
            return "agent_paused"
        if amount > snap.per_tx_cap:
            return "over_per_tx_cap"
        if to not in snap.allowlist:
            return "not_allowlisted"
        if self.spent + amount > snap.daily_cap:
            return "over_daily_cap"
        if amount > self.balance:
            return "insufficient_funds"
        return "allowed"

    def apply(self, amount_near: str) -> None:
        """Record a payment the vault really made."""
        amount = _yocto(amount_near)
        self.spent += amount
        self.balance -= amount


def precheck(snap: Snapshot, cfg: Config, mode: str, attacks: int) -> list[str]:
    """Reasons why a run now would prove nothing. An empty list means go."""
    problems: list[str] = []
    if snap.all_paused:
        problems.append("the kill switch is on (all agents paused); resume with the owner account")
    if snap.agent_paused:
        problems.append("the agent is paused; resume it with the owner account")
    if list(snap.allowlist) != [cfg.payee]:
        problems.append(f"the allowlist must be exactly [{cfg.payee}], found {list(snap.allowlist)}")
    if snap.per_tx_cap < _yocto(SPLIT_NEAR):
        problems.append(f"the per-payment cap {_near(snap.per_tx_cap)} is below {SPLIT_NEAR} NEAR; the split attack would test the wrong rule")
    if snap.per_tx_cap >= _yocto(OVERSIZED_NEAR):
        problems.append(f"the per-payment cap {_near(snap.per_tx_cap)} is not below {OVERSIZED_NEAR} NEAR; the oversized attack would be allowed")
    need = _yocto(SPLIT_NEAR) if mode == "compromised" else attacks * _yocto(AUTHORIZED_NEAR)
    headroom = max(0, snap.daily_cap - snap.spent)
    if headroom < need:
        problems.append(
            f"only {_near(headroom)} of the daily cap is left and this run needs {_near(need)}; "
            "run make policy-testnet, use a fresh LEASH_PREFIX, or wait for the window to reset"
        )
    if snap.balance < need:
        problems.append(f"the vault holds {_near(snap.balance)} and this run needs {_near(need)}; fund it")
    return problems
