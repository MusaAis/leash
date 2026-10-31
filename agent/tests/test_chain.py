import unittest

from leash_agent.chain import STDOUT_SENTINEL, ChainError, Vault, near_to_yocto, parse_call_output
from leash_agent.config import Config
from tests.fixtures import SPEND_FALSE, SPEND_TRUE, SPLIT_STDERR_SPEND, SPLIT_STDERR_VIEW, VIEW_STATS


def make_config() -> Config:
    return Config(
        network="testnet", vault="v.testnet", owner="o.testnet", agent="a.testnet",
        payee="p.testnet", attacker="x.testnet", lookalike="p3.testnet",
        ai_base_url="http://localhost", ai_model="m", ai_api_key="k",
    )


class NearToYocto(unittest.TestCase):
    def test_exact_conversions(self):
        self.assertEqual(near_to_yocto("1"), "1000000000000000000000000")
        self.assertEqual(near_to_yocto("0.5"), "500000000000000000000000")
        self.assertEqual(near_to_yocto("0.000000000000000000000001"), "1")
        self.assertEqual(near_to_yocto("1000000"), "1" + "0" * 30)

    def test_rejects_bad_values(self):
        for bad in ["0", "-1", "abc", "", "NaN", "Infinity", "0.0000000000000000000000001"]:
            with self.assertRaises(ValueError, msg=bad):
                near_to_yocto(bad)


class ParseOutput(unittest.TestCase):
    def test_allowed(self):
        result = parse_call_output(SPEND_TRUE)
        self.assertIs(result.returned, True)
        self.assertEqual(result.tx_id, "8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA")
        self.assertEqual(result.events[0]["event"], "spend_allowed")
        self.assertIsNone(result.denial_reason)

    def test_denied(self):
        result = parse_call_output(SPEND_FALSE)
        self.assertIs(result.returned, False)
        self.assertEqual(result.denial_reason, "not_allowlisted")

    def test_view_json(self):
        self.assertEqual(parse_call_output(VIEW_STATS).returned, ["2", "3"])


class ParseSplitStreams(unittest.TestCase):
    """When piped, the near CLI prints the bare value on stdout and the rest on stderr."""

    def test_denied_value_on_stdout(self):
        result = parse_call_output(SPLIT_STDERR_SPEND + STDOUT_SENTINEL + "false\n")
        self.assertIs(result.returned, False)
        self.assertEqual(result.denial_reason, "not_allowlisted")
        self.assertEqual(result.tx_id, "8xbaUvmVhx9QVXSXzM9RwHRc2S8gPfNC8GDgTFG3StHf")

    def test_allowed_value_on_stdout(self):
        result = parse_call_output(SPLIT_STDERR_SPEND + STDOUT_SENTINEL + "true\n")
        self.assertIs(result.returned, True)

    def test_view_json_on_stdout(self):
        result = parse_call_output(SPLIT_STDERR_VIEW + STDOUT_SENTINEL + '[\n  "2",\n  "3"\n]\n')
        self.assertEqual(result.returned, ["2", "3"])

    def test_string_value(self):
        result = parse_call_output("noise" + STDOUT_SENTINEL + '"leashmusa-owner.testnet"\n')
        self.assertEqual(result.returned, "leashmusa-owner.testnet")

    def test_empty_stdout_means_no_result(self):
        self.assertIsNone(parse_call_output(SPLIT_STDERR_SPEND + STDOUT_SENTINEL).returned)


class VaultClient(unittest.TestCase):
    def test_spend_builds_expected_command(self):
        seen = []
        vault = Vault(make_config(), runner=lambda args: seen.append(args) or SPEND_TRUE)
        result = vault.spend("p.testnet", "0.5", "invoice")
        self.assertTrue(result.returned)
        args = seen[0]
        self.assertIn("as-transaction", args)
        self.assertIn("sign-as", args)
        self.assertEqual(args[args.index("sign-as") + 1], "a.testnet")
        self.assertIn('"amount": "500000000000000000000000"', args[args.index("json-args") + 1])

    def test_spend_without_result_raises(self):
        vault = Vault(make_config(), runner=lambda args: "no output here")
        with self.assertRaises(ChainError):
            vault.spend("p.testnet", "1")

    def test_stats(self):
        vault = Vault(make_config(), runner=lambda args: VIEW_STATS)
        self.assertEqual(vault.stats(), (2, 3))


if __name__ == "__main__":
    unittest.main()
