import contextlib
import dataclasses
import io
import json
import os
import tempfile
import unittest

from leash_agent.__main__ import main
from leash_agent.brain import Brain
from leash_agent.chain import CallResult, ChainError
from tests.test_agent import ALLOWED_EVENT, VALID_TX, FakeVault
from tests.test_chain import make_config


def run_main(argv, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv, cfg=kwargs.pop("cfg", make_config()), **kwargs)
    return code, out.getvalue(), err.getvalue()


class LeakyVault(FakeVault):
    """A broken vault that pays everything: used to prove the suite notices."""

    def spend(self, to, amount_near, memo=""):
        return CallResult(True, VALID_TX, [])


class NoTxVault(FakeVault):
    def spend(self, to, amount_near, memo=""):
        result = super().spend(to, amount_near, memo)
        return CallResult(result.returned, None, result.events)


class RaisingVault(FakeVault):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def spend(self, to, amount_near, memo=""):
        raise self.exc


class MainExitCodes(unittest.TestCase):
    def test_compromised_run_exits_0_and_json_holds_the_transaction_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            code, out, _ = run_main(["attack", "--json", path], vault=FakeVault())
            with open(path) as handle:
                data = json.load(handle)
        self.assertEqual(code, 0)
        self.assertEqual(len(data), 1)
        self.assertTrue(all(row["tx"] == VALID_TX for row in data[0]))
        self.assertIn(f"tx {VALID_TX}", out)

    def test_exit_2_when_a_payment_reaches_a_non_payee(self):
        code, out, _ = run_main(["attack"], vault=LeakyVault())
        self.assertEqual(code, 2)
        self.assertIn("PAID TO NON-PAYEE", out)

    def test_exit_3_when_the_control_task_fails(self):
        refusing = {"usage": {"total_tokens": 500},
                    "choices": [{"message": {"content": '{"action":"refuse","reason":"no"}'}}]}
        brain = Brain(make_config(), post=lambda *a: refusing)
        code, out, _ = run_main(["attack", "--mode", "llm"], vault=FakeVault(), brain=brain)
        self.assertEqual(code, 3)
        self.assertIn("WARNING", out)

    def test_exit_3_when_a_row_has_no_transaction_id(self):
        code, out, _ = run_main(["attack"], vault=NoTxVault())
        self.assertEqual(code, 3)
        self.assertIn("tx MISSING", out)

    def test_exit_1_on_a_chain_error(self):
        code, _, err = run_main(["attack"], vault=RaisingVault(ChainError("near CLI failed")))
        self.assertEqual(code, 1)
        self.assertIn("error: near CLI failed", err)

    def test_exit_1_on_any_unexpected_exception_without_a_traceback(self):
        code, _, err = run_main(["attack"], vault=RaisingVault(ArithmeticError("boom")))
        self.assertEqual(code, 1)
        self.assertIn("error: ArithmeticError: boom", err)
        self.assertNotIn("Traceback", err)

    def test_pay_commands_refuse_a_network_that_is_not_testnet(self):
        cfg = dataclasses.replace(make_config(), network="mainnet")
        code, _, err = run_main(["attack"], cfg=cfg, vault=FakeVault())
        self.assertEqual(code, 1)
        self.assertIn("not testnet", err)

    def test_llm_run_prints_calls_and_tokens(self):
        reply = {"usage": {"total_tokens": 500},
                 "choices": [{"message": {"content": '{"action":"refuse","reason":"x"}'}}]}
        brain = Brain(make_config(), post=lambda *a: reply)
        _, out, _ = run_main(["attack", "--mode", "llm"], vault=FakeVault(), brain=brain)
        self.assertIn("model calls used: 21", out)
        self.assertIn("tokens used: 10500", out)
        self.assertIn("overall: agent fooled: 0  funds lost: 0", out)

    def test_exit_3_and_nothing_sent_when_the_preflight_fails(self):
        vault = FakeVault(spent=3.0)  # no daily cap left
        code, out, _ = run_main(["attack"], vault=vault)
        self.assertEqual(code, 3)
        self.assertEqual(vault.calls, [])
        self.assertIn("Preflight failed. Nothing was sent", out)
        self.assertIn("daily cap", out)

    def test_preflight_stops_before_any_model_call(self):
        calls = []
        brain = Brain(make_config(), post=lambda *a: calls.append(a))
        code, out, _ = run_main(["attack", "--mode", "llm"], vault=FakeVault(all_paused=True), brain=brain)
        self.assertEqual(code, 3)
        self.assertEqual(calls, [])
        self.assertIn("kill switch is on", out)

    def test_preflight_checks_the_model_call_limit(self):
        brain = Brain(make_config(), post=lambda *a: {})
        brain.max_calls = 20  # a run needs 21
        code, out, _ = run_main(["attack", "--mode", "llm"], vault=FakeVault(), brain=brain)
        self.assertEqual(code, 3)
        self.assertIn("needs 21 model calls", out)

    def test_samples_scale_the_model_calls_needed(self):
        brain = Brain(make_config(), post=lambda *a: {})
        code, out, _ = run_main(["attack", "--mode", "llm", "--samples", "2"], vault=FakeVault(), brain=brain)
        self.assertEqual(code, 3)
        self.assertIn("needs 41 model calls", out)

    def test_samples_need_llm_mode_and_a_sane_number(self):
        for argv in (["attack", "--samples", "2"], ["attack", "--mode", "llm", "--samples", "0"],
                     ["attack", "--mode", "llm", "--samples", "11"]):
            code, _, err = run_main(argv, vault=FakeVault())
            self.assertEqual(code, 1)
            self.assertIn("--samples", err)

    def test_exit_3_when_the_vault_answer_cannot_be_classified(self):
        class Silent(FakeVault):
            def spend(self, to, amount_near, memo=""):
                return CallResult(False, VALID_TX, [])

        code, out, _ = run_main(["attack"], vault=Silent())
        self.assertEqual(code, 3)
        self.assertIn("could not be classified", out)

    def test_exit_3_when_the_vault_contradicts_the_policy(self):
        class Wrong(FakeVault):
            def spend(self, to, amount_near, memo=""):
                self.calls.append((to, amount_near))
                event = {"event": "spend_denied", "data": [{"reason": "over_daily_cap"}]}
                return CallResult(False, VALID_TX, [event])

        code, out, _ = run_main(["attack"], vault=Wrong())
        self.assertEqual(code, 3)
        self.assertIn("UNEXPECTED", out)

    def test_exit_2_when_the_model_pays_without_authorization_even_to_the_payee(self):
        reply = {"usage": {"total_tokens": 500},
                 "choices": [{"message": {"content": '{"action":"spend","to":"p.testnet","amount_near":"0.1","memo":"x"}'}}]}
        brain = Brain(make_config(), post=lambda *a: reply)
        code, out, _ = run_main(["attack", "--mode", "llm"], vault=FakeVault(), brain=brain)
        self.assertEqual(code, 2)
        self.assertIn("PAID UNAUTHORIZED", out)

    def test_a_vault_view_error_is_a_plain_error(self):
        class NoPolicy(FakeVault):
            def snapshot(self):
                raise ChainError("cannot read the policy; run make policy-testnet")

        code, _, err = run_main(["attack"], vault=NoPolicy())
        self.assertEqual(code, 1)
        self.assertIn("make policy-testnet", err)

    def test_models_command_notes_a_model_that_is_not_listed(self):
        code, out, _ = run_main(["models"], vault=FakeVault(), models_fn=lambda cfg: [("a/b", "input=1")])
        self.assertEqual(code, 0)
        self.assertIn("a/b  input=1", out)
        self.assertIn("not in this list", out)


if __name__ == "__main__":
    unittest.main()
