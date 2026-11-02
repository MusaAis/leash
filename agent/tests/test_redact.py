import contextlib
import io
import tempfile
import unittest
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent.intents import IntentsError, OneClick
from leash_agent.redact import scrub
from tests.test_chain import make_config
from tests.test_guardian import FakeOneClick
from tests.test_intents import ASSET

RECIPIENT = "0xAbCdEf0123456789"
REFUND = "refund.account.near"
ENV = {"LEASH_ROUTE_RECIPIENT": RECIPIENT, "LEASH_DRY_REFUND_TO": REFUND}
HIDDEN_FORMS = [RECIPIENT, RECIPIENT.lower(), RECIPIENT.upper(), REFUND, REFUND.lower(), REFUND.upper()]


class Fake(FakeOneClick):
    def tokens(self):
        return [{"assetId": ASSET, "symbol": "USDC", "blockchain": "base", "decimals": 6, "price": 1.0}]


class Scrub(unittest.TestCase):
    def test_hides_both_values_in_any_casing(self):
        with mock.patch.dict("os.environ", ENV):
            text = scrub(f"a {RECIPIENT.upper()} b {REFUND.lower()} c {RECIPIENT}")
        self.assertEqual(text, "a <RECIPIENT> b <REFUND_TO> c <RECIPIENT>")

    def test_hides_the_value_without_its_prefix_and_percent_encoded(self):
        recipient = "0xAbCdEf0123456789"
        refund = "refund account@near"
        env = {"LEASH_ROUTE_RECIPIENT": recipient, "LEASH_DRY_REFUND_TO": refund}
        with mock.patch.dict("os.environ", env):
            text = scrub(f"x {recipient[2:]} y {recipient[2:].lower()} z refund%20account%40near w REFUND%20ACCOUNT%40NEAR")
        self.assertEqual(text, "x <RECIPIENT> y <RECIPIENT> z <REFUND_TO> w <REFUND_TO>")

    def test_nothing_set_changes_nothing(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(scrub("plain text"), "plain text")


class NothingPrivateIsPrinted(unittest.TestCase):
    def run_cli(self, fake, argv=("intents-dry", "--amount", "0.5")):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            env = {**ENV, "LEASH_GUARDIAN_LOG": f"{tmp}/g.jsonl"}
            with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = cli.main(list(argv), cfg=make_config(), vault=mock.Mock(), oneclick=fake)
        return code, out.getvalue() + err.getvalue()

    def assert_hidden(self, output):
        for form in HIDDEN_FORMS:
            self.assertNotIn(form, output)

    def test_a_changed_recipient_is_refused_without_printing_either_value(self):
        for echoed in ("0xAttacker", RECIPIENT.upper()):
            def tamper(response, echoed=echoed):
                response["quoteRequest"]["recipient"] = echoed
            code, output = self.run_cli(Fake(tamper))
            self.assertEqual(code, 1)
            self.assertIn("response changed recipient", output)
            self.assert_hidden(output)

    def test_a_changed_refund_account_is_refused_without_printing_either_value(self):
        for echoed in ("attacker.near", REFUND.upper()):
            def tamper(response, echoed=echoed):
                response["quoteRequest"]["refundTo"] = echoed
            code, output = self.run_cli(Fake(tamper))
            self.assertEqual(code, 1)
            self.assertIn("response changed refundTo", output)
            self.assert_hidden(output)

    def test_an_error_that_carries_the_values_is_scrubbed(self):
        class Leaky(Fake):
            def quote(self, req):
                raise IntentsError(f"1Click returned HTTP 400: bad recipient {RECIPIENT.lower()} refund {REFUND.upper()}")
        code, output = self.run_cli(Leaky())
        self.assertEqual(code, 1)
        self.assertIn("<RECIPIENT>", output)
        self.assert_hidden(output)

    def test_an_odd_status_answer_is_not_echoed(self):
        client = OneClick(http=lambda method, url, headers, body: {"quoteResponse": {"recipient": RECIPIENT}})
        with self.assertRaises(IntentsError) as ctx:
            client.status("a" * 64)
        self.assertNotIn(RECIPIENT, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
