import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from leash_agent import callpath
from leash_agent.__main__ import main
from leash_agent.chain import STDOUT_SENTINEL, ChainError, Vault
from leash_agent.config import Config, EnvFileError, intent_gas_tgas
from tests.fixtures import SPEND_TRUE

RECIPIENT = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
HEX64 = "ab" * 32
NS = 1_000_000_000


def make_config(network="testnet", **extra) -> Config:
    suffix = "near" if network == "mainnet" else "testnet"
    return Config(
        network=network, vault=f"v.{suffix}", owner=f"o.{suffix}", agent=f"a.{suffix}", payee=f"p.{suffix}",
        attacker=f"x.{suffix}", lookalike=f"p3.{suffix}", ai_base_url="http://localhost", ai_model="m", ai_api_key="k",
        guardian=f"g.{suffix}", wnear="wrap.near" if network == "mainnet" else "wrap.testnet", **extra,
    )


def value_after(args, key):
    return args[args.index(key) + 1]


def payload(args):
    return json.loads(value_after(args, "json-args"))


class Recorder:
    def __init__(self, output="ok", error=None):
        self.calls, self.output, self.error = [], output, error

    def __call__(self, args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.output + STDOUT_SENTINEL


class Gas(unittest.TestCase):
    def test_default_is_150_and_bounds_are_120_to_300(self):
        self.assertEqual(intent_gas_tgas(None), 150)
        self.assertEqual(intent_gas_tgas(""), 150)
        self.assertEqual(intent_gas_tgas("120"), 120)
        self.assertEqual(intent_gas_tgas("300"), 300)

    def test_out_of_range_and_odd_values_are_refused(self):
        for bad in ["119", "301", "0", "-150", "1e2", "150.0", "abc", "\u0661\u0665\u0660", "150 Tgas"]:
            with self.assertRaises(EnvFileError, msg=bad):
                intent_gas_tgas(bad)

    def test_spend_and_spend_intent_use_separate_defaults(self):
        cfg = make_config()
        self.assertEqual(cfg.gas, "30 Tgas")
        self.assertEqual(cfg.intent_gas, "150 Tgas")
        spend = Recorder(SPEND_TRUE)
        Vault(cfg, runner=spend).spend("p.testnet", "0.5")
        self.assertEqual(value_after(spend.calls[0], "prepaid-gas"), "30 Tgas")
        args = callpath.spend_intent_args(cfg, 0, HEX64, "0.1")
        self.assertEqual(value_after(args, "prepaid-gas"), "150 Tgas")
        self.assertNotEqual(value_after(args, "prepaid-gas"), cfg.gas)

    def test_env_value_reaches_the_config(self):
        with mock.patch.dict(os.environ, {"LEASH_INTENT_GAS_TGAS": "200", "NETWORK": "testnet"}):
            self.assertEqual(Config.from_env().intent_gas, "200 Tgas")
        with mock.patch.dict(os.environ, {"LEASH_INTENT_GAS_TGAS": "50"}), self.assertRaises(EnvFileError):
            Config.from_env()


class Builders(unittest.TestCase):
    def test_config_is_owner_signed_with_one_yocto(self):
        args = callpath.set_intents_config_args(make_config())
        self.assertEqual(value_after(args, "sign-as"), "o.testnet")
        self.assertEqual(value_after(args, "attached-deposit"), "1 yoctoNEAR")
        self.assertEqual(args[5], "set_intents_config")
        self.assertEqual(payload(args), {"wnear": "wrap.testnet", "guardian": "g.testnet"})

    def test_routes_carry_the_recipient_only_in_the_payload(self):
        args = callpath.set_routes_args(make_config(), "nep141:base-usdc", RECIPIENT)
        self.assertEqual(value_after(args, "sign-as"), "o.testnet")
        self.assertEqual(value_after(args, "attached-deposit"), "1 yoctoNEAR")
        self.assertEqual(payload(args), {"agent": "a.testnet", "routes": [{"dest_asset": "nep141:base-usdc", "recipient": RECIPIENT}]})
        for missing in [("", RECIPIENT), ("nep141:x", ""), ("", "")]:
            with self.assertRaises(callpath.CallPathError):
                callpath.set_routes_args(make_config(), *missing)

    def test_approve_is_guardian_signed_without_deposit(self):
        now = 5_000 * NS
        args = callpath.approve_intent_args(make_config(), 0, HEX64, "0.1", 20, HEX64, now_ns=now)
        self.assertEqual(value_after(args, "sign-as"), "g.testnet")
        self.assertEqual(value_after(args, "attached-deposit"), "0 NEAR")
        self.assertEqual(
            payload(args),
            {"agent": "a.testnet", "route_id": 0, "deposit_account": HEX64, "amount": "100000000000000000000000",
             "expires_ns": str(now + 20 * 60 * NS), "quote_hash": HEX64},
        )

    def test_approve_lifetime_stays_inside_the_contract_limit(self):
        for minutes in (0, 30, -1):
            with self.assertRaises(callpath.CallPathError):
                callpath.approve_intent_args(make_config(), 0, HEX64, "0.1", minutes, HEX64)
        callpath.approve_intent_args(make_config(), 0, HEX64, "0.1", 1, HEX64)
        callpath.approve_intent_args(make_config(), 0, HEX64, "0.1", 29, HEX64)

    def test_approve_refuses_bad_hashes_routes_and_accounts(self):
        for bad in ["", "AB" * 32, "ab" * 31, "zz" * 32, HEX64 + "0"]:
            with self.assertRaises(callpath.CallPathError, msg=bad):
                callpath.approve_intent_args(make_config(), 0, HEX64, "0.1", 20, bad)
        with self.assertRaises(callpath.CallPathError):
            callpath.approve_intent_args(make_config(), 255, HEX64, "0.1", 20, HEX64)
        with self.assertRaises(callpath.CallPathError):
            callpath.approve_intent_args(make_config(), 0, "Bad Account", "0.1", 20, HEX64)
        with self.assertRaises(ValueError):
            callpath.approve_intent_args(make_config(), 0, HEX64, "0", 20, HEX64)

    def test_spend_intent_is_agent_signed_with_its_own_gas(self):
        args = callpath.spend_intent_args(make_config(intent_gas_tgas=200), 0, HEX64, "0.1")
        self.assertEqual(value_after(args, "sign-as"), "a.testnet")
        self.assertEqual(value_after(args, "prepaid-gas"), "200 Tgas")
        self.assertEqual(value_after(args, "attached-deposit"), "0 NEAR")
        self.assertEqual(payload(args), {"route_id": 0, "deposit_account": HEX64, "amount": "100000000000000000000000"})

    def test_status_is_four_read_only_views(self):
        views = callpath.status_commands(make_config())
        self.assertEqual([name for name, _ in views], ["get_approvals", "get_stats", "get_policy", "get_available_balance"])
        for _, args in views:
            self.assertIn("as-read-only", args)
            self.assertNotIn("sign-as", args)


class Execute(unittest.TestCase):
    def test_plan_only_sends_nothing(self):
        run, lines = Recorder(), []
        cfg = make_config()
        out = callpath.execute(cfg, callpath.spend_intent_args(cfg, 0, HEX64, "0.1"), [cfg.vault, cfg.agent, HEX64],
                               confirmed=False, runner=run, out=lines.append)
        self.assertIsNone(out)
        self.assertEqual(run.calls, [])
        self.assertIn("prepaid gas 150 Tgas", lines[0])

    def test_confirmed_sends_once(self):
        run = Recorder("done")
        cfg = make_config()
        out = callpath.execute(cfg, callpath.spend_intent_args(cfg, 0, HEX64, "0.1"), [cfg.vault, cfg.agent, HEX64],
                               confirmed=True, runner=run, out=lambda _t: None)
        self.assertEqual(len(run.calls), 1)
        self.assertIn("done", out)

    def test_mainnet_needs_the_process_variable_and_nothing_is_sent_without_it(self):
        cfg, run = make_config("mainnet"), Recorder()
        args = callpath.spend_intent_args(cfg, 0, HEX64, "0.1")
        accounts = [cfg.vault, cfg.agent, HEX64]
        for env in ({}, {"LEASH_ALLOW_MAINNET": "true"}, {"LEASH_ALLOW_MAINNET": "0"}):
            with self.assertRaises(callpath.CallPathError):
                callpath.execute(cfg, args, accounts, confirmed=True, runner=run, env=env, out=lambda _t: None)
        self.assertEqual(run.calls, [])
        callpath.execute(cfg, args, accounts, confirmed=True, runner=run, env={"LEASH_ALLOW_MAINNET": "1"}, out=lambda _t: None)
        self.assertEqual(len(run.calls), 1)

    def test_a_testnet_account_on_mainnet_is_refused(self):
        cfg, run = make_config("mainnet"), Recorder()
        args = callpath.spend_intent_args(cfg, 0, "d.testnet", "0.1")
        with self.assertRaises(callpath.CallPathError):
            callpath.execute(cfg, args, [cfg.vault, cfg.agent, "d.testnet"], confirmed=True, runner=run,
                             env={"LEASH_ALLOW_MAINNET": "1"}, out=lambda _t: None)
        self.assertEqual(run.calls, [])

    def test_private_values_are_scrubbed_from_output_and_errors(self):
        cfg = make_config()
        args = callpath.set_routes_args(cfg, "nep141:x", RECIPIENT)
        accounts = [cfg.vault, cfg.owner, cfg.agent]
        with mock.patch.dict(os.environ, {"LEASH_ROUTE_RECIPIENT": RECIPIENT}):
            shown = []
            out = callpath.execute(cfg, args, accounts, confirmed=True, runner=Recorder(f"echo {RECIPIENT}"), out=shown.append)
            self.assertNotIn(RECIPIENT, out)
            self.assertIn("<RECIPIENT>", out)
            self.assertNotIn(RECIPIENT, "\n".join(shown))
            with self.assertRaises(callpath.CallPathError) as ctx:
                callpath.execute(cfg, args, accounts, confirmed=True, runner=Recorder(error=ChainError(f"failed {RECIPIENT}")), out=lambda _t: None)
            self.assertNotIn(RECIPIENT, str(ctx.exception))


class Cli(unittest.TestCase):
    def run_main(self, argv, cfg, runner, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
            code = main(argv, cfg=cfg, runner=runner)
        return code, out.getvalue(), err.getvalue()

    def test_spend_without_yes_is_a_plan(self):
        run = Recorder()
        code, out, _ = self.run_main(["intents-spend", "--deposit-account", HEX64, "--amount", "0.1"], make_config(), run)
        self.assertEqual((code, run.calls), (0, []))
        self.assertIn("plan only", out)

    def test_spend_with_yes_sends_one_transaction(self):
        run = Recorder("sent")
        code, out, _ = self.run_main(["intents-spend", "--deposit-account", HEX64, "--amount", "0.1", "--yes"], make_config(), run)
        self.assertEqual(code, 0)
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0][5], "spend_intent")

    def test_mainnet_commands_are_refused_without_the_variable(self):
        for argv in (["intents-config", "--yes"], ["intents-routes", "--yes"],
                     ["intents-spend", "--deposit-account", HEX64, "--amount", "0.1", "--yes"],
                     ["intents-approve", "--deposit-account", HEX64, "--amount", "0.1", "--quote-hash", HEX64, "--yes"],
                     ["intents-status"]):
            run = Recorder()
            env = {"LEASH_ROUTE_DEST_ASSET": "nep141:x", "LEASH_ROUTE_RECIPIENT": RECIPIENT}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("LEASH_ALLOW_MAINNET", None)
                code, _, err = self.run_main(argv, make_config("mainnet"), run, env)
            self.assertEqual(code, 1, argv)
            self.assertEqual(run.calls, [], argv)
            self.assertIn("LEASH_ALLOW_MAINNET", err)

    def test_approve_outside_testnet_needs_an_explicit_quote_hash(self):
        run = Recorder()
        code, _, err = self.run_main(["intents-approve", "--deposit-account", HEX64, "--amount", "0.1", "--yes"],
                                     make_config("mainnet"), run, {"LEASH_ALLOW_MAINNET": "1"})
        self.assertEqual(code, 1)
        self.assertEqual(run.calls, [])
        self.assertIn("--quote-hash", err)

    def test_testnet_approve_uses_the_fixed_rehearsal_hash(self):
        run = Recorder()
        code, _, _ = self.run_main(["intents-approve", "--deposit-account", HEX64, "--amount", "0.1", "--yes"], make_config(), run)
        self.assertEqual(code, 0)
        self.assertEqual(payload(run.calls[0])["quote_hash"], callpath.REHEARSAL_QUOTE_HASH)
        self.assertEqual(value_after(run.calls[0], "sign-as"), "g.testnet")

    def test_status_runs_four_views_and_no_transaction(self):
        run = Recorder("[]")
        code, out, _ = self.run_main(["intents-status"], make_config(), run)
        self.assertEqual(code, 0)
        self.assertEqual(len(run.calls), 4)
        self.assertTrue(all("as-read-only" in c for c in run.calls))
        self.assertIn("-- get_available_balance", out)

    def test_a_failed_call_is_a_plain_error(self):
        run = Recorder(error=ChainError("near CLI failed: boom"))
        code, _, err = self.run_main(["intents-spend", "--deposit-account", HEX64, "--amount", "0.1", "--yes"], make_config(), run)
        self.assertEqual(code, 1)
        self.assertIn("boom", err)

    def test_routes_read_the_environment_and_never_print_the_recipient(self):
        run = Recorder("ok")
        env = {"LEASH_ROUTE_DEST_ASSET": "nep141:x", "LEASH_ROUTE_RECIPIENT": RECIPIENT}
        code, out, err = self.run_main(["intents-routes", "--yes"], make_config(), run, env)
        self.assertEqual(code, 0)
        self.assertEqual(payload(run.calls[0])["routes"][0]["recipient"], RECIPIENT)
        self.assertNotIn(RECIPIENT, out + err)


if __name__ == "__main__":
    unittest.main()
