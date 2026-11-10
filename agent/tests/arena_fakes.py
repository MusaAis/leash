"""Fakes for the Arena tests: a brain that returns set decisions and a vault that records calls."""

from leash_agent.brain import Decision
from leash_agent.chain import CallResult, ChainError
from leash_arena.tiers import FORTRESS, LOOSE, Tier

TX = "8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA"
PAYEE = "arenapayee.testnet"
BAIT = "arenabait.testnet"


def fortress() -> Tier:
    return Tier(FORTRESS, "arenaf-vault.testnet", "arenaf-agent.testnet", PAYEE, "0.01")


def loose() -> Tier:
    return Tier(LOOSE, "arenal-vault.testnet", "arenal-agent.testnet", PAYEE, "0.01", bait=BAIT)


class FakeBrain:
    def __init__(self, decision=None, error=None, tokens=100):
        self.decision, self.error, self.tokens_used = decision, error, 0
        self._tokens = tokens
        self.tasks = []

    def decide(self, task):
        self.tasks.append(task)
        self.tokens_used += self._tokens
        if self.error:
            raise self.error
        return self.decision


class BrainSource:
    """A factory that hands out one fresh brain per attempt and remembers them."""

    def __init__(self, decision=None, error=None, tokens=100):
        self.args = (decision, error, tokens)
        self.brains = []

    def __call__(self):
        brain = FakeBrain(*self.args)
        self.brains.append(brain)
        return brain


class FakePayer:
    def __init__(self, returned=True, reason=None, error=None):
        self.returned, self.reason, self.error, self.calls = returned, reason, error, []

    def spend(self, to, amount_near, memo=""):
        self.calls.append((to, amount_near, memo))
        if self.error:
            raise self.error
        events = [] if self.returned else [{"event": "spend_denied", "data": [{"reason": self.reason}]}]
        return CallResult(returned=self.returned, tx_id=TX, events=events)


def spend(to, amount="0.01", memo="invoice"):
    return Decision("spend", to=to, amount_near=amount, memo=memo)


def refuse(reason="no"):
    return Decision("refuse", reason=reason)


__all__ = ["ChainError", "Decision"]
