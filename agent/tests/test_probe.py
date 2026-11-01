import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent.guardian import GuardianLog
from leash_agent.intents import IntentsError, QuoteRejected
from leash_agent.probe import MAX_PROBE_YOCTO, address_kind, wet_probe
from tests.test_chain import make_config
from tests.test_guardian import FakeOneClick
from tests.test_intents import ASSET, DEPOSIT, NOW

NEAR = 10**24


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = GuardianLog(Path(self.tmp.name) / "g.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, fake, amount=NEAR // 10, **over):
        args = dict(dest_asset=ASSET, recipient="0xRecipient", refund_to="r.near", amount=amount, now=NOW)
        args.update(over)
        return wet_probe(fake, self.log, **args)

    def test_a_good_quote_is_logged_in_full_under_its_own_event(self):
        _, verified, response = self.probe(FakeOneClick())
        self.assertEqual(verified.deposit_address, DEPOSIT)
        entry = self.log.entries()[-1]
        self.assertEqual(entry["event"], "probe_quote")
        self.assertEqual(entry["response"], response)
        self.assertNotIn("quote", {e["event"] for e in self.log.entries()})  # the guardian's limits never count it

    def test_the_amount_is_limited_before_any_call(self):
        fake = FakeOneClick()
        for amount in (0, -1, MAX_PROBE_YOCTO + 1, True, "1"):
            with self.assertRaises(IntentsError):
                self.probe(fake, amount=amount)
        self.assertEqual(fake.quotes, [])
        self.probe(fake, amount=MAX_PROBE_YOCTO)  # the limit itself is allowed

    def test_a_bad_answer_is_rejected_logged_and_attached_to_the_error(self):
        def tamper(response):
            response["quote"]["depositAddress"] = "v.near"
        with self.assertRaises(QuoteRejected) as ctx:
            self.probe(FakeOneClick(tamper), forbidden=("v.near",))
        self.assertEqual(ctx.exception.response["quote"]["depositAddress"], "v.near")
        self.assertEqual(self.log.entries()[-1]["event"], "probe_rejected")

    def test_address_kinds(self):
        self.assertEqual(address_kind("a" * 64), "implicit (64 hex characters)")
        self.assertEqual(address_kind("deposit.near"), "named account")
        self.assertEqual(address_kind("odd"), "other")


class ProbeCli(unittest.TestCase):
    class Fake(FakeOneClick):
        def tokens(self):
            return [{"assetId": ASSET, "symbol": "USDC", "blockchain": "base", "decimals": 6, "price": 1.0}]

    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        fake = self.Fake()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LEASH_GUARDIAN_LOG": f"{tmp}/g.jsonl", "LEASH_ROUTE_RECIPIENT": "0xRecipient", **env}
            with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = cli.main(argv, cfg=make_config(), vault=mock.Mock(), oneclick=fake)
        return code, out.getvalue(), err.getvalue(), fake

    def test_prints_the_address_and_format_hides_the_recipient_and_warns(self):
        code, out, _, _ = self.run_main(["intents-wet-probe", "--yes"], {"LEASH_DRY_REFUND_TO": "r.near"})
        self.assertEqual(code, 0)
        self.assertIn(f"deposit address: {DEPOSIT}", out)
        self.assertIn("implicit (64 hex characters)", out)
        self.assertIn("DO NOT SEND ANY FUNDS", out)
        self.assertNotIn("0xRecipient", out)
        self.assertIn("<RECIPIENT>", out)

    def test_without_yes_nothing_is_asked(self):
        code, _, err, fake = self.run_main(["intents-wet-probe"], {"LEASH_DRY_REFUND_TO": "r.near"})
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(fake.quotes, [])

    def test_needs_a_refund_account_and_refuses_a_big_amount(self):
        code, _, err, fake = self.run_main(["intents-wet-probe", "--yes"], {})
        self.assertEqual(code, 1)
        self.assertIn("LEASH_DRY_REFUND_TO", err)
        code, _, err, fake = self.run_main(["intents-wet-probe", "--yes", "--amount", "0.2"], {"LEASH_DRY_REFUND_TO": "r.near"})
        self.assertEqual(code, 1)
        self.assertIn("at most 0.15", err)
        self.assertEqual(fake.quotes, [])


if __name__ == "__main__":
    unittest.main()
