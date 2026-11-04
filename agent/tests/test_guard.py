import contextlib
import io
import unittest
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent.guard import GuardError, check

IMPLICIT = "a" * 64
MAIN = {"LEASH_ALLOW_MAINNET": "1"}


class Guard(unittest.TestCase):
    def test_mainnet_needs_the_flag_in_the_process_environment(self):
        for env in ({}, {"LEASH_ALLOW_MAINNET": ""}):
            with self.assertRaises(GuardError):
                check("mainnet", ["v.near"], env)
        self.assertEqual(check("mainnet", ["v.near"], MAIN), ["v.near"])

    def test_only_the_exact_value_1_switches_mainnet_on(self):
        for value in ("0", "false", "no", "", " ", "1 ", "true", "yes"):
            with self.assertRaises(GuardError, msg=repr(value)):
                check("mainnet", ["v.near"], {"LEASH_ALLOW_MAINNET": value})

    def test_mainnet_refuses_testnet_accounts_and_accepts_the_rest(self):
        with self.assertRaises(GuardError):
            check("mainnet", ["v.near", "x.testnet"], MAIN)
        self.assertEqual(check("mainnet", ["a.near", "b.tg", IMPLICIT, "sub.a.near"], MAIN)[2], IMPLICIT)

    def test_testnet_refuses_named_accounts_that_are_not_testnet(self):
        for bad in ("v.near", "v.tg", "plain"):
            with self.assertRaises(GuardError, msg=bad):
                check("testnet", ["a.testnet", bad], {})
        self.assertEqual(check("testnet", ["a.testnet", IMPLICIT], {}), ["a.testnet", IMPLICIT])

    def test_odd_input_is_refused(self):
        for network, accounts in (("devnet", ["a.near"]), ("testnet", []), ("testnet", ["A.testnet"]), ("mainnet", ["a..near"]), ("mainnet", ["x"]), ("mainnet", ["has space.near"])):
            with self.assertRaises(GuardError, msg=(network, accounts)):
                check(network, accounts, MAIN)

    def test_the_flag_is_read_from_the_environment_each_time_not_stored(self):
        check("mainnet", ["v.near"], MAIN)
        with self.assertRaises(GuardError):
            check("mainnet", ["v.near"], {})


class GuardCli(unittest.TestCase):
    def run_cli(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_the_command_prints_every_account_and_ok(self):
        code, out, _ = self.run_cli(["guard", "--network", "mainnet", "v.near", IMPLICIT], MAIN)
        self.assertEqual(code, 0)
        self.assertIn("v.near", out)
        self.assertIn(IMPLICIT, out)
        self.assertTrue(out.strip().endswith("OK"))

    def test_a_refusal_exits_1_with_a_message_and_no_ok(self):
        code, out, err = self.run_cli(["guard", "--network", "mainnet", "x.testnet"], MAIN)
        self.assertEqual(code, 1)
        self.assertNotIn("OK", out)
        self.assertIn("testnet account", err)

    def test_it_needs_no_env_file(self):
        code, _, _ = self.run_cli(["guard", "--network", "testnet", "a.testnet"], {})
        self.assertEqual(code, 0)


class NonTestnetFlag(unittest.TestCase):
    def pay(self, value):
        env = {"LEASH_ALLOW_NON_TESTNET": value} if value is not None else {}
        err = io.StringIO()
        cfg = mock.Mock(network="mainnet")
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(cli.Config, "from_env", return_value=cfg), contextlib.redirect_stderr(err):
            code = cli.main(["attack"])
        return code, err.getvalue()

    def test_any_value_but_1_still_refuses_a_paying_command_off_testnet(self):
        for value in (None, "0", "false", "", " "):
            code, err = self.pay(value)
            self.assertEqual(code, 1, msg=repr(value))
            self.assertIn("LEASH_ALLOW_NON_TESTNET", err)
