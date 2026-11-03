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


POLICY = json.dumps({"per_tx_cap": "500000000000000000000000", "daily_cap": "1000000000000000000000000",
                     "spent_in_window": "250000000000000000000000", "paused": False})


class Recorder:
    """Transactions go to `calls`, read-only views to `views` (`get_policy` answers with POLICY)."""

    def __init__(self, output="ok", error=None, policy=POLICY):
        self.calls, self.views, self.output, self.error, self.policy = [], [], output, error, policy

    def __call__(self, args):
        if "as-read-only" in args:
            self.views.append(args)
            if args[5] == "get_policy" and self.policy is not None:
                return STDOUT_SENTINEL + self.policy
            return self.output + STDOUT_SENTINEL
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
        for argv in (["intents-config", "--yes", "--confirm-vault", "v.near"], ["intents-routes", "--yes", "--confirm-vault", "v.near"],
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

    def test_approve_outside_testnet_refuses_a_typed_hash_or_deposit_account(self):
        from pathlib import Path
        import tempfile
        from leash_agent.intents import utc_now
        from tests.test_swap import quote_entry
        from leash_agent.guardian import GuardianLog
        cfg = make_config("mainnet")
        for extra in (["--quote-hash", HEX64], ["--deposit-account", HEX64]):
            with tempfile.TemporaryDirectory() as tmp:
                log = GuardianLog(Path(tmp) / "g.jsonl")
                log.append(quote_entry(cfg, utc_now()))  # a valid saved quote exists: only the refusal stops the send
                run = Recorder()
                env = {"LEASH_ALLOW_MAINNET": "1", "LEASH_GUARDIAN_LOG": str(log.path)}
                code, _, err = self.run_main(["intents-approve", "--amount", "0.1", "--yes", *extra], cfg, run, env)
                self.assertEqual((code, run.calls, run.views), (1, [], []), extra)
                self.assertIn("do not pass them", err)
                self.assertEqual([e["event"] for e in log.entries()], ["quote_issued"])

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
        self.assertEqual((len(run.views), run.calls), (4, []))
        self.assertIn("-- get_available_balance", out)

    def test_a_failed_call_is_a_plain_error(self):
        run = Recorder(error=ChainError("near CLI failed: boom"))
        code, _, err = self.run_main(["intents-spend", "--deposit-account", HEX64, "--amount", "0.1", "--yes"], make_config(), run)
        self.assertEqual(code, 1)
        self.assertIn("boom", err)

    def test_routes_read_the_environment_and_never_print_the_recipient(self):
        run = Recorder("ok")
        env = {"LEASH_ROUTE_DEST_ASSET": "nep141:x", "LEASH_ROUTE_RECIPIENT": RECIPIENT}
        code, out, err = self.run_main(["intents-routes", "--yes", "--confirm-vault", "v.testnet"], make_config(), run, env)
        self.assertEqual(code, 0)
        self.assertEqual(payload(run.calls[0])["routes"][0]["recipient"], RECIPIENT)
        self.assertNotIn(RECIPIENT, out + err)


def tx_output(returned, events=()):
    lines = "".join("EVENT_JSON:" + json.dumps({"standard": "leash", "event": name, "data": [data]}) + "\n" for name, data in events)
    return lines + STDOUT_SENTINEL + returned


class PlanDetails(unittest.TestCase):
    """The plan shows what will be sent, never the recipient."""

    def plan(self, args, cfg=None, runner=None, env=None):
        cfg, lines = cfg or make_config(), []
        accounts = [cfg.vault, cfg.owner, cfg.guardian, cfg.agent, HEX64]
        callpath.execute(cfg, args, accounts, confirmed=False, runner=runner or Recorder(), out=lines.append, env=env)
        return "\n".join(lines)

    def test_spend_plan_has_route_deposit_amount_and_the_agents_caps(self):
        cfg = make_config()
        text = self.plan(callpath.spend_intent_args(cfg, 0, HEX64, "0.1"), cfg)
        for part in ("route 0", HEX64, "amount 0.1 NEAR", "per-tx 0.5 NEAR", "daily 1 NEAR", "already used in this window 0.25 NEAR"):
            self.assertIn(part, text)

    def test_approve_plan_has_the_expiry_in_utc(self):
        cfg = make_config()
        text = self.plan(callpath.approve_intent_args(cfg, 0, HEX64, "0.1", 20, HEX64, now_ns=1_800_000_000 * NS), cfg)
        self.assertIn("approval expires 2027-01-15 08:20:00 UTC", text)
        self.assertIn("amount 0.1 NEAR", text)

    def test_owner_plans_say_what_changes_and_hide_the_recipient(self):
        cfg = make_config()
        with mock.patch.dict(os.environ, {"LEASH_ROUTE_RECIPIENT": RECIPIENT}):
            routes = self.plan(callpath.set_routes_args(cfg, "nep141:x", RECIPIENT), cfg)
        self.assertIn("1 route(s) for a.testnet", routes)
        self.assertIn("recipient not shown", routes)
        self.assertNotIn(RECIPIENT, routes)
        self.assertIn("wNEAR contract wrap.testnet, guardian g.testnet", self.plan(callpath.set_intents_config_args(cfg), cfg))

    def test_amounts_are_exact(self):
        self.assertEqual(callpath.yocto_to_near("100000000000000000000000"), "0.1")
        self.assertEqual(callpath.yocto_to_near("1250000000000000000000"), "0.00125")
        self.assertEqual(callpath.yocto_to_near(10**24), "1")
        self.assertEqual(callpath.yocto_to_near(0), "0")

    def test_unreadable_caps_stop_a_confirmed_send_outside_testnet_only(self):
        mainnet = make_config("mainnet")
        run = Recorder(policy=None)
        args = callpath.spend_intent_args(mainnet, 0, HEX64, "0.1")
        with self.assertRaisesRegex(callpath.CallPathError, "caps could not be read"):
            callpath.execute(mainnet, args, [mainnet.vault, mainnet.agent, HEX64], confirmed=True, runner=run,
                             env={"LEASH_ALLOW_MAINNET": "1"}, out=lambda _t: None)
        self.assertEqual(run.calls, [])
        testnet, run = make_config(), Recorder(policy=None)
        lines = []
        callpath.execute(testnet, callpath.spend_intent_args(testnet, 0, HEX64, "0.1"), [testnet.vault, testnet.agent, HEX64],
                         confirmed=True, runner=run, out=lines.append)
        self.assertEqual(len(run.calls), 1)
        self.assertIn("could not be read", "\n".join(lines))


class Result(unittest.TestCase):
    """ALLOWED, DENIED or FAILED, with the note that the swap is shown elsewhere."""

    def test_allowed(self):
        line = callpath.interpret("spend_intent", tx_output("true", [("intent_sent", {"agent": "a"})]))
        self.assertTrue(line.startswith("ALLOWED: batch scheduled"))
        self.assertIn(callpath.SWAP_NOTE, line)

    def test_denied_names_the_reason_from_the_event(self):
        line = callpath.interpret("spend_intent", tx_output("false", [("intent_denied", {"reason": "over_daily_cap"})]))
        self.assertTrue(line.startswith("DENIED: over_daily_cap"))
        self.assertIn(callpath.SWAP_NOTE, line)

    def test_failed_shows_the_panic_message(self):
        raw = "Transaction failed\nSmart contract panicked: gas too low for four actions\n" + STDOUT_SENTINEL
        line = callpath.interpret("spend_intent", raw)
        self.assertTrue(line.startswith("FAILED:"))
        self.assertIn("gas too low for four actions", line)
        self.assertIn(callpath.SWAP_NOTE, line)

    def test_a_result_with_nothing_readable_is_failed_not_allowed(self):
        self.assertTrue(callpath.interpret("spend_intent", "weird" + STDOUT_SENTINEL).startswith("FAILED:"))

    def test_true_without_the_sent_event_is_not_called_allowed(self):
        self.assertTrue(callpath.interpret("spend_intent", tx_output("true")).startswith("FAILED:"))

    def test_other_methods_are_confirmed_by_their_event(self):
        self.assertTrue(callpath.interpret("approve_intent", tx_output("", [("intent_approved", {})])).startswith("DONE"))
        self.assertTrue(callpath.interpret("approve_intent", tx_output("")).startswith("UNCONFIRMED"))
        self.assertTrue(callpath.interpret("set_routes", tx_output("", [("routes_set", {})])).startswith("DONE"))
        self.assertTrue(callpath.interpret("set_intents_config", tx_output("", [("intents_configured", {})])).startswith("DONE"))

    def test_execute_prints_the_result_line_and_a_failed_call_says_failed(self):
        cfg = make_config()
        args = callpath.spend_intent_args(cfg, 0, HEX64, "0.1")
        accounts = [cfg.vault, cfg.agent, HEX64]
        raw = tx_output("false", [("intent_denied", {"reason": "agent_paused"})])
        out = callpath.execute(cfg, args, accounts, confirmed=True, runner=lambda a: raw if "as-transaction" in a else STDOUT_SENTINEL + POLICY,
                               out=lambda _t: None)
        self.assertIn("RESULT: DENIED: agent_paused", out)
        with self.assertRaisesRegex(callpath.CallPathError, "FAILED: .*boom"):
            callpath.execute(cfg, args, accounts, confirmed=True, runner=Recorder(error=ChainError("boom")), out=lambda _t: None)


class VaultName(unittest.TestCase):
    """Owner commands need the typed vault name to send."""

    def run_main(self, argv, run, cfg=None):
        out, err = io.StringIO(), io.StringIO()
        env = {"LEASH_ROUTE_DEST_ASSET": "nep141:x", "LEASH_ROUTE_RECIPIENT": RECIPIENT}
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            code = main(argv, cfg=cfg or make_config(), runner=run)
        return code, out.getvalue(), err.getvalue()

    def test_missing_and_wrong_names_send_nothing(self):
        for command in ("intents-config", "intents-routes"):
            for extra in ([], ["--confirm-vault", "other.testnet"], ["--confirm-vault", "V.testnet"], ["--confirm-vault", ""]):
                run = Recorder()
                code, _, err = self.run_main([command, "--yes", *extra], run)
                self.assertEqual((code, run.calls), (1, []), (command, extra))
                self.assertIn("--confirm-vault", err)

    def test_the_right_name_sends_and_a_plan_needs_no_name(self):
        for command in ("intents-config", "intents-routes"):
            run = Recorder()
            code, _, _ = self.run_main([command, "--yes", "--confirm-vault", "v.testnet"], run)
            self.assertEqual((code, len(run.calls)), (0, 1), command)
            run = Recorder()
            code, out, _ = self.run_main([command], run)
            self.assertEqual((code, run.calls), (0, []))
            self.assertIn("plan only", out)

    def test_approve_and_spend_do_not_need_the_name(self):
        run = Recorder()
        code, _, _ = self.run_main(["intents-spend", "--amount", "0.1", "--deposit-account", HEX64, "--yes"], run)
        self.assertEqual((code, len(run.calls)), (0, 1))


class MakeYes(unittest.TestCase):
    """Only YES=1 sends. Any other value leaves --yes out of the command."""

    TARGETS = ("intents-keys", "intents-config", "intents-routes", "intents-quote", "intents-approve", "intents-spend")

    def make(self, target, *settings):
        import shutil
        import subprocess
        if not shutil.which("make"):
            self.skipTest("make is not installed")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        proc = subprocess.run(["make", "-n", "-C", root, target, "SWAP_AMOUNT=0.1", *settings], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_other_values_never_send(self):
        for target in self.TARGETS:
            for value in ("YES=0", "YES=no", "YES=", "YES=true", "YES=yes", "YES=11", "YES=2", "YES= "):
                self.assertNotIn("--yes", self.make(target, value), (target, value))
            self.assertNotIn("--yes", self.make(target), target)

    def test_yes_1_sends(self):
        for target in self.TARGETS:
            self.assertIn("--yes", self.make(target, "YES=1"), target)

    def test_the_vault_name_and_saved_quote_variables_reach_the_command(self):
        self.assertIn("--confirm-vault v.testnet", self.make("intents-config", "CONFIRM_VAULT=v.testnet"))
        self.assertNotIn("--deposit-account", self.make("intents-spend"))
        self.assertIn("--deposit-account abc", self.make("intents-spend", "DEPOSIT=abc"))


if __name__ == "__main__":
    unittest.main()
