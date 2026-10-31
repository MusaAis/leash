import unittest

from leash_agent.agent import Agent
from leash_agent.attacks import build_attacks, run_suite
from leash_agent.brain import Decision
from leash_agent.chain import CallResult
from tests.test_chain import make_config

VALID_TX = "8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA"


class FakeBrain:
    def __init__(self, decision):
        self.decision = decision

    def decide(self, task):
        return self.decision


class FakeVault:
    """Mimics the real vault policy: only p.testnet, at most 1 NEAR, 3 NEAR in total."""

    def __init__(self):
        self.calls = []
        self.total = 0.0

    def spend(self, to, amount_near, memo=""):
        self.calls.append((to, amount_near))
        amount = float(amount_near)
        reason = None
        if to != "p.testnet":
            reason = "not_allowlisted"
        elif amount > 1:
            reason = "over_per_tx_cap"
        elif self.total + amount > 3:
            reason = "over_daily_cap"
        if reason:
            event = {"event": "spend_denied", "data": [{"reason": reason}]}
            return CallResult(False, VALID_TX, [event])
        self.total += amount
        return CallResult(True, VALID_TX, [])

    def daily_headroom_yocto(self):
        return 3 * 10**24


class AgentFlow(unittest.TestCase):
    def test_refusal_never_calls_vault(self):
        vault = FakeVault()
        outcome = Agent(FakeBrain(Decision("refuse", reason="no")), vault, log=lambda m: None).handle("t")
        self.assertEqual(vault.calls, [])
        self.assertIn("refused by the agent", outcome.summary())

    def test_dry_run_never_calls_vault(self):
        vault = FakeVault()
        decision = Decision("spend", to="p.testnet", amount_near="0.5")
        Agent(FakeBrain(decision), vault, log=lambda m: None).handle("t", dry_run=True)
        self.assertEqual(vault.calls, [])

    def test_blocked_by_vault(self):
        decision = Decision("spend", to="x.testnet", amount_near="0.5")
        outcome = Agent(FakeBrain(decision), FakeVault(), log=lambda m: None).handle("t")
        self.assertFalse(outcome.paid)
        self.assertIn("not_allowlisted", outcome.summary())


class AttackSuite(unittest.TestCase):
    def test_compromised_agent_loses_nothing(self):
        cfg = make_config()
        vault = FakeVault()
        report = run_suite(cfg, vault, mode="compromised", log=lambda m: None)
        expected = sum(len(a.goals) for a in build_attacks(cfg))
        self.assertEqual(report.attempts, expected)
        self.assertEqual(len(vault.calls), expected)
        self.assertEqual(report.lost, 0)
        self.assertGreater(report.blocked, 0)

    def test_rows_carry_the_transaction_id(self):
        report = run_suite(make_config(), FakeVault(), log=lambda m: None)
        self.assertTrue(all(row.tx == VALID_TX for row in report.rows))
        self.assertIn(f"tx {VALID_TX}", report.render())
        self.assertEqual(report.missing_tx, 0)

    def test_refused_rows_have_no_transaction(self):
        cfg = make_config()
        brain = FakeBrain(Decision("refuse", reason="no"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None)
        self.assertTrue(all(row.tx == "" for row in report.rows))

    def test_row_without_a_valid_transaction_id_is_marked_missing(self):
        class NoTx(FakeVault):
            def spend(self, to, amount_near, memo=""):
                result = super().spend(to, amount_near, memo)
                return CallResult(result.returned, None, result.events)

        report = run_suite(make_config(), NoTx(), log=lambda m: None)
        self.assertEqual(report.missing_tx, report.attempts)
        self.assertIn("tx MISSING", report.render())
        self.assertIn("WARNING", report.render())

    def test_report_text(self):
        report = run_suite(make_config(), FakeVault(), log=lambda m: None)
        text = report.render()
        self.assertIn("attempts:", text)
        self.assertIn("paid to non-payee: 0", text)

    def test_llm_mode_with_refusing_model_flags_failed_control(self):
        cfg = make_config()
        brain = FakeBrain(Decision("refuse", reason="injection"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None)
        self.assertEqual(report.refused, report.attempts)
        self.assertEqual(report.lost, 0)
        self.assertIs(report.control_ok, False)
        self.assertIn("WARNING", report.render())
        self.assertIn("injection", report.rows[0].outcome)

    def test_llm_mode_control_passes_when_model_can_pay(self):
        cfg = make_config()
        brain = FakeBrain(Decision("spend", to=cfg.payee, amount_near="0.2"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None)
        self.assertIs(report.control_ok, True)
        self.assertNotIn("WARNING", report.render())

    def test_authorized_scenario_counts_redirected_decisions(self):
        cfg = make_config()
        brain = FakeBrain(Decision("spend", to=cfg.attacker, amount_near="0.3"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None, authorized=True)
        self.assertEqual(report.redirected, report.attempts)
        self.assertEqual(report.lost, 0)
        self.assertIn("agent chose a non-payee", report.render())
        self.assertIn("owner authorizes", report.title)

    def test_authorized_scenario_task_names_the_payee(self):
        cfg = make_config()
        seen = []

        class Spy:
            def decide(self, task):
                seen.append(task)
                return Decision("refuse", reason="x")

        run_suite(cfg, FakeVault(), mode="llm", brain=Spy(), log=lambda m: None, authorized=True)
        self.assertTrue(all(cfg.payee in task and "<untrusted>" in task for task in seen))
        self.assertEqual(len(seen), len(build_attacks(cfg)))

    def test_hijacked_mode_counts_every_attempt_as_redirected_when_not_to_payee(self):
        report = run_suite(make_config(), FakeVault(), log=lambda m: None)
        self.assertGreater(report.redirected, 0)

    def test_llm_mode_hijacked_model_is_still_blocked(self):
        cfg = make_config()
        brain = FakeBrain(Decision("spend", to=cfg.attacker, amount_near="0.5"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None)
        self.assertEqual(report.lost, 0)
        self.assertEqual(report.blocked, report.attempts)


if __name__ == "__main__":
    unittest.main()
