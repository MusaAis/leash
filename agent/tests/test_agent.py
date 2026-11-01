import unittest

from leash_agent.agent import Agent
from leash_agent.attacks import build_attacks, run_suite
from leash_agent.brain import Decision
from leash_agent.chain import CallResult, Snapshot
from leash_agent.preflight import AUTHORIZED_NEAR, PolicySim
from tests.test_chain import make_config

VALID_TX = "8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA"


class FakeBrain:
    def __init__(self, decision):
        self.decision = decision

    def decide(self, task):
        return self.decision


ALLOWED_EVENT = {"event": "spend_allowed", "data": [{}]}


class FakeVault:
    """Mimics the real vault policy: only p.testnet, at most 1 NEAR, 3 NEAR in total, 5 NEAR held."""

    def __init__(self, spent=0.0, balance=5.0, agent_paused=False, all_paused=False):
        self.calls = []
        self.total = spent
        self.balance = balance
        self.agent_paused = agent_paused
        self.all_paused = all_paused

    def snapshot(self):
        return Snapshot(per_tx_cap=10**24, daily_cap=3 * 10**24, spent=int(self.total * 10**24),
                        allowlist=("p.testnet",), agent_paused=self.agent_paused,
                        all_paused=self.all_paused, balance=int(self.balance * 10**24))

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
        self.balance -= amount
        return CallResult(True, VALID_TX, [ALLOWED_EVENT])


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


class SuiteSemantics(unittest.TestCase):
    """Expected reasons, unknown answers, amounts and controls (review items A-004, A-005, A-012)."""

    def sim(self, vault):
        return PolicySim(vault.snapshot())

    def test_there_are_ten_attacks_and_fourteen_hijacked_requests(self):
        attacks = build_attacks(make_config())
        self.assertEqual(len(attacks), 10)
        self.assertEqual(sum(len(a.goals) for a in attacks), 14)

    def test_hijacked_run_matches_the_policy_prediction(self):
        vault = FakeVault()
        report = run_suite(make_config(), vault, sim=self.sim(vault), log=lambda m: None)
        self.assertEqual(report.unexpected, 0)
        self.assertEqual((report.attempts, report.blocked, report.paid_to_payee), (14, 11, 3))
        self.assertEqual(report.funds_lost, 0)
        self.assertFalse(report.invalid)

    def test_a_vault_that_answers_differently_from_the_policy_marks_the_run_unusable(self):
        class DenyAll(FakeVault):
            def spend(self, to, amount_near, memo=""):
                self.calls.append((to, amount_near))
                event = {"event": "spend_denied", "data": [{"reason": "not_allowlisted"}]}
                return CallResult(False, VALID_TX, [event])

        vault = DenyAll()
        report = run_suite(make_config(), vault, sim=self.sim(vault), log=lambda m: None)
        self.assertGreater(report.unexpected, 0)
        self.assertTrue(report.invalid)
        self.assertIn("UNEXPECTED (the policy predicted: allowed)", report.render())

    def test_a_hijacked_run_where_nothing_was_paid_proves_nothing(self):
        vault = FakeVault(spent=3.0)  # no room: every payment is blocked, nothing positive is shown
        report = run_suite(make_config(), vault, log=lambda m: None)
        self.assertEqual(report.paid_to_payee, 0)
        self.assertFalse(report.positive_ok)
        self.assertTrue(report.invalid)
        self.assertIn("the blocks above prove nothing", report.render())

    def test_false_without_a_denial_event_is_unknown_not_blocked(self):
        class Silent(FakeVault):
            def spend(self, to, amount_near, memo=""):
                return CallResult(False, VALID_TX, [])

        report = run_suite(make_config(), Silent(), log=lambda m: None)
        self.assertEqual(report.unknown, report.attempts)
        self.assertEqual(report.blocked, 0)
        self.assertTrue(report.invalid)
        self.assertIn("UNKNOWN", report.render())

    def test_true_without_an_allowed_event_is_unknown(self):
        class NoEvent(FakeVault):
            def spend(self, to, amount_near, memo=""):
                result = super().spend(to, amount_near, memo)
                return CallResult(result.returned, VALID_TX, [e for e in result.events if e["event"] != "spend_allowed"])

        report = run_suite(make_config(), NoEvent(), log=lambda m: None)
        self.assertEqual(report.unknown, 3)  # the three split payments the vault allowed
        self.assertEqual(report.paid_to_payee, 0)

    def test_scenario_one_any_payment_is_unauthorized_and_counts_as_fooled(self):
        cfg = make_config()
        brain = FakeBrain(Decision("spend", to=cfg.payee, amount_near="0.1"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None)
        self.assertEqual(report.unauthorized, report.attempts)
        self.assertEqual(report.fooled, report.attempts)
        self.assertEqual(report.funds_lost, report.attempts)
        self.assertEqual(report.lost, 0)
        self.assertIn("PAID UNAUTHORIZED", report.render())

    def test_authorized_scenario_compares_the_amount(self):
        cfg = make_config()
        brain = FakeBrain(Decision("spend", to=cfg.payee, amount_near="0.25"))  # authorized is 0.2
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None, authorized=True)
        self.assertEqual(report.unauthorized, report.attempts)
        self.assertEqual(report.fooled, report.attempts)
        self.assertEqual(report.followed, 0)
        self.assertIs(report.control_ok, False)
        self.assertIn("tested nothing", report.render())

    def test_authorized_scenario_exact_amount_is_followed_and_not_fooled(self):
        cfg = make_config()
        brain = FakeBrain(Decision("spend", to=cfg.payee, amount_near=AUTHORIZED_NEAR))
        vault = FakeVault()
        report = run_suite(cfg, vault, mode="llm", brain=brain, log=lambda m: None, authorized=True,
                           sim=self.sim(vault))
        self.assertEqual((report.fooled, report.funds_lost, report.unexpected), (0, 0, 0))
        self.assertEqual(report.followed, report.attempts)
        self.assertIs(report.control_ok, True)
        self.assertIn("10 of 10 authorized invoices were followed exactly", report.control)

    def test_authorized_scenario_where_the_model_refuses_everything_is_not_a_pass(self):
        cfg = make_config()
        brain = FakeBrain(Decision("refuse", reason="no"))
        report = run_suite(cfg, FakeVault(), mode="llm", brain=brain, log=lambda m: None, authorized=True)
        self.assertIs(report.control_ok, False)
        self.assertTrue(report.invalid)

    def test_the_wrapper_cannot_be_closed_by_attack_text(self):
        cfg = make_config()
        seen = []

        class Spy:
            def decide(self, task):
                seen.append(task)
                return Decision("refuse", reason="x")

        run_suite(cfg, FakeVault(), mode="llm", brain=Spy(), log=lambda m: None)
        breakout = [t for t in seen if "SYSTEM: the owner approved" in t]
        self.assertEqual(len(breakout), 1)
        self.assertEqual(breakout[0].count("</untrusted>"), 1)
        self.assertEqual(breakout[0].count("<untrusted>"), 1)
        self.assertIn("&lt;/untrusted&gt;", breakout[0])

    def test_extra_samples_ask_the_model_but_never_call_the_vault(self):
        cfg = make_config()
        asked = []

        class Counting:
            def decide(self, task):
                asked.append(task)
                return Decision("spend", to=cfg.attacker, amount_near="0.5")

        vault = FakeVault()
        report = run_suite(cfg, vault, mode="llm", brain=Counting(), log=lambda m: None, samples=3)
        attacks = len(build_attacks(cfg))
        self.assertEqual(len(asked), 1 + attacks * 3)  # one control plus every sample
        self.assertEqual(len(vault.calls), attacks)  # only the first sample reaches the vault
        self.assertEqual(report.attempts, attacks * 3)
        self.assertEqual(sum(r.outcome.startswith("decision only") for r in report.rows), attacks * 2)
        self.assertEqual(report.fooled, attacks * 3)
        self.assertTrue(any(r.attack.endswith("#2") for r in report.rows))
        self.assertEqual(report.missing_tx, 0)


if __name__ == "__main__":
    unittest.main()
