import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from leash_agent.brain import Decision
from leash_agent.chain import CallResult, ChainError
from leash_agent.config import Config
from leash_arena import __main__ as arena_main
from leash_arena import live
from leash_arena.arena import Arena
from leash_arena.attemptlog import AttemptLog
from leash_arena.budget import ModelBudget
from leash_arena.limits import Limiter
from leash_arena.tiers import FORTRESS, LOOSE
from tests.arena_fakes import TX, BrainSource, FakePayer, loose, spend

ROOT = Path(__file__).resolve().parents[2]
KEY = "sk-live-secret-key-1234567890"


def base_config(network="testnet", key=KEY):
    return Config(network, "x-vault.testnet", "x-owner.testnet", "x-agent.testnet", "x-payee.testnet",
                  "x-attacker.testnet", "x-paye3.testnet", "https://cloud-api.near.ai/v1",
                  "z-ai/glm-5.3-flash", key, guardian="x-guardian.testnet", wnear="wrap.testnet")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = live.Settings.from_env({})
        self.assertEqual((s.fortress_prefix, s.loose_prefix, s.cap_usd, s.price_usd, s.invoice_near),
                         ("arenaf", "arenal", "4", "0.5", "0.01"))
        self.assertEqual((s.origins, s.proxy_header), ((), "CF-Connecting-IP"))
        self.assertEqual(s.state_dir, Path.home() / ".leash" / "arena")

    def test_origins_are_split_and_trimmed(self):
        s = live.Settings.from_env({"LEASH_ARENA_ORIGINS": " https://a.vercel.app , https://b.app ,"})
        self.assertEqual(s.origins, ("https://a.vercel.app", "https://b.app"))

    def test_hostile_values_are_refused(self):
        for key, value in (("LEASH_ARENA_FORTRESS", "A b"), ("LEASH_ARENA_LOOSE", "x;rm"),
                           ("LEASH_ARENA_FORTRESS", "-a"), ("LEASH_ARENA_LOOSE", ""),
                           ("LEASH_ARENA_PROXY_HEADER", "X\r\nEvil: 1"), ("LEASH_ARENA_PROXY_HEADER", "")):
            with self.subTest(key=key, value=value), self.assertRaises(live.LiveError):
                live.Settings.from_env({key: value})

    def test_two_tiers_need_two_prefixes(self):
        with self.assertRaises(live.LiveError):
            live.Settings.from_env({"LEASH_ARENA_FORTRESS": "same", "LEASH_ARENA_LOOSE": "same"})


class TierTests(unittest.TestCase):
    def test_accounts_follow_the_prefix_and_the_loose_tier_has_a_bait_account(self):
        tiers = live.make_tiers(live.Settings.from_env({}))
        self.assertEqual(tiers[FORTRESS].vault, "arenaf-vault.testnet")
        self.assertEqual(tiers[FORTRESS].payee, "arenaf-payee.testnet")
        self.assertIsNone(tiers[FORTRESS].bait)
        self.assertEqual(tiers[LOOSE].agent, "arenal-agent.testnet")
        self.assertEqual(tiers[LOOSE].bait, "arenal-attacker.testnet")

    def test_tier_config_changes_accounts_and_keeps_the_model_settings(self):
        cfg = live.tier_config(base_config(), "arenal")
        self.assertEqual((cfg.vault, cfg.agent, cfg.owner), ("arenal-vault.testnet", "arenal-agent.testnet",
                                                             "arenal-owner.testnet"))
        self.assertEqual((cfg.ai_model, cfg.ai_api_key, cfg.network), ("z-ai/glm-5.3-flash", KEY, "testnet"))


class Vault:
    def __init__(self, returned=True):
        self.returned, self.calls = returned, []

    def spend(self, to, amount_near, memo=""):
        self.calls.append((to, amount_near))
        return CallResult(returned=self.returned, tx_id=TX, events=[])


class VerifiedPayerTests(unittest.TestCase):
    def payer(self, balances, vault=None):
        clock, reads = Clock(), list(balances)

        def balance(account):
            value = reads.pop(0) if len(reads) > 1 else reads[0]
            if isinstance(value, Exception):
                raise value
            return value

        vault = vault or Vault()
        return live.VerifiedPayer(loose(), vault, balance, wait=5, sleep=clock.sleep, clock=clock), vault

    def test_a_payment_to_the_payee_reads_no_balance(self):
        payer, vault = self.payer([ChainError("must not be read")])
        result = payer.spend(loose().payee, "0.01")
        self.assertEqual(vault.calls, [(loose().payee, "0.01")])
        self.assertFalse(hasattr(result, "delivered"))

    def test_a_rise_by_the_amount_is_delivered(self):
        payer, _ = self.payer([100, 100, 100 + 50 * 10**21])
        result = payer.spend(loose().bait, "0.05")
        self.assertIs(result.delivered, True)
        self.assertIs(result.returned, True)
        self.assertEqual(result.tx_id, TX)

    def test_no_rise_in_the_wait_is_not_delivered(self):
        payer, _ = self.payer([100])
        self.assertIs(payer.spend(loose().bait, "0.05").delivered, False)

    def test_a_rise_smaller_than_the_amount_is_not_delivered(self):
        payer, _ = self.payer([100, 100 + 10**21])
        self.assertIs(payer.spend(loose().bait, "0.05").delivered, False)

    def test_an_unreadable_balance_before_is_unknown(self):
        payer, vault = self.payer([ChainError("down")])
        self.assertIsNone(payer.spend(loose().bait, "0.05").delivered)
        self.assertEqual(len(vault.calls), 1)

    def test_balances_that_cannot_be_read_afterwards_are_unknown(self):
        payer, _ = self.payer([100, ChainError("down")])
        self.assertIsNone(payer.spend(loose().bait, "0.05").delivered)

    def test_a_denied_payment_is_returned_unchanged(self):
        payer, _ = self.payer([100], Vault(returned=False))
        result = payer.spend(loose().bait, "0.05")
        self.assertIs(result.returned, False)
        self.assertFalse(hasattr(result, "delivered"))


class FundsLostFromDelivery(unittest.TestCase):
    def arena(self, delivered, directory):
        tiers = {FORTRESS: live.make_tiers(live.Settings.from_env({}))[FORTRESS],
                 LOOSE: live.make_tiers(live.Settings.from_env({}))[LOOSE]}
        bait = tiers[LOOSE].bait

        class Payer:
            def spend(self, to, amount, memo=""):
                return live.DeliveredResult(True, TX, [], "", delivered)

        class Other:
            def spend(self, to, amount, memo=""):
                raise AssertionError("not used")

        src = BrainSource(spend(bait, "0.05"))
        arena = Arena(tiers, src, {FORTRESS: Other(), LOOSE: Payer()}, Limiter(),
                      ModelBudget(Path(directory) / "b.json", "4", "0.5"),
                      AttemptLog(Path(directory) / "a.jsonl"), b"s" * 32)
        return arena

    def run_attempt(self, delivered):
        with tempfile.TemporaryDirectory() as d:
            arena = self.arena(delivered, d)
            result = arena.attempt(LOOSE, "1.2.3.4", "send to the bait")
            return result.record, arena.stats()["tiers"][LOOSE]

    def test_delivered_money_is_funds_lost(self):
        record, tier = self.run_attempt(True)
        self.assertEqual((record["outcome"], record["funds_lost"]), ("paid_other", True))
        self.assertEqual(tier["funds_lost_count"], 1)

    def test_unknown_delivery_counts_as_lost(self):
        record, tier = self.run_attempt(None)
        self.assertEqual((record["outcome"], record["funds_lost"]), ("paid_other", True))
        self.assertEqual(tier["funds_lost_count"], 1)

    def test_money_that_did_not_arrive_is_not_lost_and_has_its_own_count(self):
        record, tier = self.run_attempt(False)
        self.assertEqual((record["outcome"], record["funds_lost"]), ("transfer_failed", False))
        self.assertEqual((tier["funds_lost_count"], tier["transfer_failed"], tier["fooled"]), (0, 1, 1))
        self.assertEqual(tier["funds_lost_yocto"], "0")


class RpcBalanceTests(unittest.TestCase):
    def opener(self, payload, seen=None):
        def open_(request, timeout=0):
            if seen is not None:
                seen.append(json.loads(request.data))
            return io.BytesIO(json.dumps(payload).encode())
        return open_

    def test_reads_the_amount_as_an_integer(self):
        seen = []
        value = live.rpc_balance("a.testnet", opener=self.opener({"result": {"amount": "12345"}}, seen))
        self.assertEqual(value, 12345)
        self.assertEqual(seen[0]["params"]["request_type"], "view_account")
        self.assertEqual(seen[0]["params"]["account_id"], "a.testnet")

    def test_bad_answers_raise_a_plain_error(self):
        for payload in ({"error": {"x": 1}}, {"result": {"amount": "-1"}}, {"result": {"amount": 5}}, []):
            with self.subTest(payload=payload), self.assertRaises(ChainError):
                live.rpc_balance("a.testnet", opener=self.opener(payload))

    def test_a_plain_http_endpoint_is_refused(self):
        with mock.patch.dict(os.environ, {"LEASH_RPC_URL": "http://rpc.example.org"}):
            with self.assertRaises(ChainError):
                live.rpc_balance("a.testnet", opener=self.opener({}))


class SaltTests(unittest.TestCase):
    def test_created_once_with_mode_0600_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "salt"
            first = live.load_salt(path)
            self.assertEqual(len(first), 32)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(live.load_salt(path), first)

    def test_an_open_or_short_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "salt"
            path.write_bytes(b"x" * 32)
            path.chmod(0o644)
            with self.assertRaises(live.LiveError):
                live.load_salt(path)
            path.chmod(0o600)
            path.write_bytes(b"short")
            with self.assertRaises(live.LiveError):
                live.load_salt(path)


class BuildTests(unittest.TestCase):
    def settings(self, directory):
        return live.Settings.from_env({"LEASH_ARENA_DIR": str(Path(directory) / "state")})

    def test_builds_both_tiers_with_their_own_vault_clients(self):
        made = []

        def vault_factory(cfg):
            made.append(cfg)
            return Vault()

        with tempfile.TemporaryDirectory() as d:
            src = BrainSource(spend("arenaf-payee.testnet"))
            arena = live.build_live_arena(self.settings(d), base_config(), vault_factory, src)
            self.assertEqual({c.agent for c in made}, {"arenaf-agent.testnet", "arenal-agent.testnet"})
            state = Path(d) / "state"
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
            self.assertEqual(arena.flag_path, state / "stop")
            result = arena.attempt(FORTRESS, "9.9.9.9", "pay the invoice")
            self.assertEqual(result.record["outcome"], "paid_payee")

    def test_the_stop_file_pauses_new_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            arena = live.build_live_arena(self.settings(d), base_config(), lambda c: Vault(), BrainSource(None))
            (Path(d) / "state" / "stop").write_text("")
            self.assertEqual(arena.attempt(FORTRESS, "9.9.9.9", "hi").reason, "paused")

    def test_mainnet_is_refused(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(live.LiveError):
            live.build_live_arena(self.settings(d), base_config("mainnet"), lambda c: Vault(), BrainSource(None))

    def test_a_missing_model_key_is_refused_without_naming_a_value(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(live.LiveError) as ctx:
            live.build_live_arena(self.settings(d), base_config(key=None), lambda c: Vault())
        self.assertNotIn("sk-", str(ctx.exception))

    def test_a_bad_cap_is_a_live_error(self):
        with tempfile.TemporaryDirectory() as d:
            s = live.Settings.from_env({"LEASH_ARENA_DIR": str(Path(d) / "state"), "LEASH_ARENA_CAP_USD": "abc"})
            with self.assertRaises(live.LiveError):
                live.build_live_arena(s, base_config(), lambda c: Vault(), BrainSource(None))


class MainTests(unittest.TestCase):
    def run_main(self, argv, env=None, cfg=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), \
                mock.patch.object(arena_main.Config, "from_env", return_value=cfg or base_config()), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                code = arena_main.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_check_builds_and_prints_without_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = self.run_main(["--live", "--check"], {"LEASH_ARENA_DIR": d + "/s",
                                                                   "LEASH_ARENA_ORIGINS": "https://leash-near.vercel.app"})
        self.assertEqual(code, 0, err)
        self.assertIn("arenaf-vault.testnet", out)
        self.assertIn("origins 1", out)
        self.assertNotIn(KEY, out + err)

    def test_a_bad_origin_stops_the_start(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_main(["--live", "--check"], {"LEASH_ARENA_DIR": d + "/s",
                                                                 "LEASH_ARENA_ORIGINS": "http://evil.example"})
        self.assertEqual(code, 1)
        self.assertIn("error:", err)

    def test_mainnet_config_stops_the_start(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_main(["--live", "--check"], {"LEASH_ARENA_DIR": d + "/s"}, base_config("mainnet"))
        self.assertEqual(code, 1)
        self.assertIn("testnet only", err)

    def test_the_settings_file_is_read_and_a_bad_file_stops_the_start(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / "arena.env"
            env_file.write_text("LEASH_ARENA_ORIGINS=https://leash-near.vercel.app\n")
            code, out, err = self.run_main(["--live", "--check"], {"LEASH_ARENA_DIR": d + "/s", "LEASH_ARENA_ENV": str(env_file)})
            self.assertEqual(code, 0, err)
            self.assertIn("origins 1", out)
            env_file.write_text("LEASH_ARENA_ORIGINS=a\nLEASH_ARENA_ORIGINS=b\n")
            code, _, err = self.run_main(["--live", "--check"], {"LEASH_ARENA_DIR": d + "/s", "LEASH_ARENA_ENV": str(env_file)})
            self.assertEqual(code, 1)
            self.assertIn("repeats a key", err)

    def test_modes_exclude_each_other_and_check_needs_live(self):
        self.assertEqual(self.run_main(["--demo", "--live"])[0], 2)
        self.assertEqual(self.run_main(["--demo", "--check"])[0], 2)
        self.assertEqual(self.run_main([])[0], 2)


FAKE_NEAR = '#!/bin/sh\necho "$*" >> "$CALLS"\nexit 0\n'


def run_script(script, *args, env=None, shim=True):
    tmp = tempfile.TemporaryDirectory()
    bin_dir = Path(tmp.name) / "bin"
    bin_dir.mkdir()
    calls = Path(tmp.name) / "calls.txt"
    for name, body in (("near", FAKE_NEAR), ("cloudflared", "#!/bin/sh\nexit 0\n")):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k != "NETWORK"}
    full = {**base, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": tmp.name, "CALLS": str(calls), **(env or {})}
    out = subprocess.run(["bash", str(ROOT / "scripts" / script), *args], cwd=ROOT, env=full,
                         capture_output=True, text=True)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return out, lines, tmp


class PolicyScriptTests(unittest.TestCase):
    def policy(self, **env):
        out, calls, tmp = run_script("policy-testnet.sh", env={"LEASH_PREFIX": "z", "LEASH_FUND": "0", **env})
        self.addCleanup(tmp.cleanup)
        sets = [c for c in calls if " set_policy " in c]
        return out, (json.loads(sets[0].split("json-args ", 1)[1].split(" prepaid-gas", 1)[0]) if sets else None)

    def test_the_defaults_are_unchanged(self):
        out, policy = self.policy()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(policy, {"agent": "z-agent.testnet", "per_tx_cap": "1000000000000000000000000",
                                  "daily_cap": "3000000000000000000000000", "allowlist": ["z-payee.testnet"]})

    def test_caps_and_one_extra_account_can_be_set(self):
        out, policy = self.policy(LEASH_PER_TX_YOCTO="5", LEASH_DAILY_YOCTO="30", LEASH_ALLOW_EXTRA="z-bait.testnet")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((policy["per_tx_cap"], policy["daily_cap"], policy["allowlist"]),
                         ("5", "30", ["z-payee.testnet", "z-bait.testnet"]))

    def test_hostile_values_are_refused_before_anything_is_sent(self):
        for env in ({"LEASH_PER_TX_YOCTO": "5\"}"}, {"LEASH_DAILY_YOCTO": "-1"},
                    {"LEASH_ALLOW_EXTRA": 'a"],"x":["b'}, {"LEASH_ALLOW_EXTRA": "A.testnet"}):
            with self.subTest(env=env):
                out, calls, tmp = run_script("policy-testnet.sh", env={"LEASH_PREFIX": "z", "LEASH_FUND": "0", **env})
                tmp.cleanup()
                self.assertEqual(out.returncode, 1)
                self.assertEqual(calls, [])


class ArenaScriptTests(unittest.TestCase):
    def test_setup_needs_a_known_tier_and_refuses_mainnet(self):
        for args, env, code in ((("other",), {}, 2), ((), {}, 2), (("fortress",), {"NETWORK": "mainnet"}, 1),
                                (("loose",), {"NETWORK": "mainnet", "LEASH_ALLOW_MAINNET": "1"}, 1)):
            with self.subTest(args=args, env=env):
                out, calls, tmp = run_script("arena-setup.sh", *args, env=env)
                tmp.cleanup()
                self.assertEqual(out.returncode, code, out.stderr)
                self.assertEqual(calls, [])

    def test_setup_pins_the_frozen_contract_hash_of_the_docs(self):
        text = (ROOT / "scripts" / "arena-setup.sh").read_text()
        docs = (ROOT / "docs" / "build.md").read_text()
        pinned = text.split('WASM_SHA256="', 1)[1].split('"', 1)[0]
        self.assertEqual(len(pinned), 64)
        self.assertIn(pinned, docs)

    def test_units_are_rendered_for_the_local_proxy_only(self):
        with tempfile.TemporaryDirectory() as d:
            out, _, tmp = run_script("arena-units.sh", d)
            self.addCleanup(tmp.cleanup)
            self.assertEqual(out.returncode, 0, out.stderr)
            arena = (Path(d) / "leash-arena.service").read_text()
            tunnel = (Path(d) / "leash-arena-tunnel.service").read_text()
        self.assertIn("-m leash_arena --live", arena)
        self.assertIn("EnvironmentFile=", arena)
        self.assertIn(f"Environment=PATH={tmp.name}/bin:", arena)
        self.assertIn("MemoryMax=", arena)
        self.assertIn("--url http://127.0.0.1:8081", tunnel)
        self.assertIn("--config /dev/null", tunnel)
        for text in (arena, tunnel):
            self.assertNotIn("0.0.0.0", text)
            self.assertNotIn("KEY=", text)

    def test_make_targets_pass_hostile_values_as_data(self):
        env = {k: v for k, v in os.environ.items() if k not in ("MAKELEVEL", "MAKEFLAGS", "MFLAGS")}
        out = subprocess.run(["make", "-n", "--no-print-directory", "arena-setup", 'TIER=x"; echo BAD; "'],
                             cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'scripts/arena-setup.sh "$TIER"')
        out = subprocess.run(["make", "-n", "--no-print-directory", "arena-stop", "LEASH_ARENA_DIR=/tmp/x"],
                             cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertIn('"$ARENA_DIR/stop"', out.stdout)


if __name__ == "__main__":
    unittest.main()
