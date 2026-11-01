import dataclasses
import unittest

from leash_agent.chain import Snapshot
from leash_agent.preflight import PolicySim, precheck
from tests.test_chain import make_config

NEAR = 10**24


def snap(**over):
    base = dict(per_tx_cap=NEAR, daily_cap=3 * NEAR, spent=0, allowlist=("p.testnet",),
                agent_paused=False, all_paused=False, balance=5 * NEAR)
    base.update(over)
    return Snapshot(**base)


class Prediction(unittest.TestCase):
    def test_allowed_payment(self):
        self.assertEqual(PolicySim(snap()).peek("p.testnet", "0.5"), "allowed")

    def test_each_reason_in_the_contracts_order(self):
        cases = [
            (snap(all_paused=True, agent_paused=True), "x.testnet", "5", "all_paused"),
            (snap(agent_paused=True), "x.testnet", "5", "agent_paused"),
            (snap(), "x.testnet", "5", "over_per_tx_cap"),   # the cap is checked before the allowlist
            (snap(), "x.testnet", "0.5", "not_allowlisted"),
            (snap(spent=int(2.5 * NEAR)), "p.testnet", "0.9", "over_daily_cap"),
            (snap(balance=NEAR // 10), "p.testnet", "0.5", "insufficient_funds"),
        ]
        for s, to, amount, reason in cases:
            self.assertEqual(PolicySim(s).peek(to, amount), reason, reason)

    def test_exactly_at_the_cap_is_allowed(self):
        self.assertEqual(PolicySim(snap(spent=2 * NEAR)).peek("p.testnet", "1"), "allowed")

    def test_peek_does_not_change_state_and_apply_does(self):
        sim = PolicySim(snap())
        sim.peek("p.testnet", "0.9")
        self.assertEqual((sim.spent, sim.balance), (0, 5 * NEAR))
        for _ in range(3):
            sim.apply("0.9")
        self.assertEqual(sim.peek("p.testnet", "0.9"), "over_daily_cap")


class Precheck(unittest.TestCase):
    cfg = make_config()

    def test_a_clean_vault_passes_in_both_modes(self):
        self.assertEqual(precheck(snap(), self.cfg, "compromised", 10), [])
        self.assertEqual(precheck(snap(), self.cfg, "llm", 10), [])

    def test_pause_flags_fail(self):
        self.assertTrue(any("kill switch" in p for p in precheck(snap(all_paused=True), self.cfg, "llm", 10)))
        self.assertTrue(any("agent is paused" in p for p in precheck(snap(agent_paused=True), self.cfg, "llm", 10)))

    def test_the_allowlist_must_be_exactly_the_payee(self):
        for allowlist in (("p.testnet", "x.testnet"), ("x.testnet",), ()):
            problems = precheck(snap(allowlist=allowlist), self.cfg, "llm", 10)
            self.assertTrue(any("allowlist" in p for p in problems), allowlist)

    def test_caps_must_suit_the_split_and_oversized_attacks(self):
        self.assertTrue(any("split" in p for p in precheck(snap(per_tx_cap=NEAR // 2), self.cfg, "llm", 10)))
        self.assertTrue(any("oversized" in p for p in precheck(snap(per_tx_cap=5 * NEAR), self.cfg, "llm", 10)))

    def test_headroom_and_balance_are_needed(self):
        low = precheck(snap(spent=int(2.5 * NEAR)), self.cfg, "llm", 10)
        self.assertTrue(any("daily cap is left" in p and "2.00 NEAR" in p for p in low))
        # the hijacked run needs one split payment (0.9 NEAR): 1.0 left is enough, 0.5 is not
        self.assertEqual(precheck(snap(spent=2 * NEAR), self.cfg, "compromised", 10), [])
        self.assertTrue(precheck(snap(spent=int(2.5 * NEAR)), self.cfg, "compromised", 10))
        poor = precheck(snap(balance=NEAR), self.cfg, "llm", 10)
        self.assertTrue(any("vault holds 1.00 NEAR" in p for p in poor))
