import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from leash_agent import keys
from leash_agent.callpath import CallPathError
from leash_agent.chain import STDOUT_SENTINEL
from tests.test_callpath import make_config, value_after

ROOT = Path(__file__).resolve().parents[2]
NEAR = 10**24


def fc(allowance, methods, receiver="v.testnet"):
    return {"FunctionCall": {"allowance": allowance, "receiver_id": receiver, "method_names": methods}}


def answer(*perms):
    keys_ = [{"public_key": f"ed25519:K{i}", "access_key": {"nonce": i, "permission": p}} for i, p in enumerate(perms)]
    return {"result": {"keys": keys_}}


GOOD = {
    "g.testnet": answer(fc(str(2 * 10**22), ["approve_intent"])),
    "a.testnet": answer(fc(str(8 * 10**22), ["spend", "spend_intent"])),
}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return STDOUT_SENTINEL


class Keys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.lines = Path(self.tmp.name), []

    def run_keys(self, cfg=None, confirmed=True, fetch=None, runner=None, env=None):
        runner = runner or Recorder()
        code = keys.run(cfg or make_config(), confirmed=confirmed, runner=runner, env=env, out=self.lines.append,
                        fetch=fetch or (lambda account, network: GOOD[account]), home=self.home)
        return code, runner

    def test_the_guardian_key_is_limited_to_approve_intent_on_the_vault(self):
        args = keys.add_key_args(make_config(), "g.testnet", keys.GUARDIAN_METHODS, keys.GUARDIAN_ALLOWANCE)
        self.assertEqual(args[3], "g.testnet")
        self.assertEqual(value_after(args, "--method-names"), "approve_intent")
        self.assertEqual(value_after(args, "--receiver-account-id"), "v.testnet")
        self.assertEqual(value_after(args, "--allowance"), "0.02 NEAR")
        self.assertEqual(value_after(args, "sign-with-legacy-keychain"), "send")

    def test_the_agent_key_allows_exactly_spend_and_spend_intent(self):
        args = keys.add_key_args(make_config(), "a.testnet", keys.AGENT_METHODS, keys.AGENT_ALLOWANCE)
        self.assertEqual(value_after(args, "--method-names"), "spend,spend_intent")
        self.assertEqual(value_after(args, "--allowance"), "0.08 NEAR")

    def test_allowances_cover_five_agent_calls_and_six_guardian_calls(self):
        price = 0.0001  # NEAR per Tgas at the minimum gas price
        self.assertGreaterEqual(float(keys.AGENT_ALLOWANCE), 5 * 150 * price)
        self.assertGreaterEqual(float(keys.GUARDIAN_ALLOWANCE), 6 * 30 * price)

    def test_plan_only_sends_nothing_and_lists_every_key(self):
        code, run = self.run_keys(confirmed=False)
        text = "\n".join(self.lines)
        self.assertEqual((code, run.calls), (0, []))
        for part in ("g.testnet: methods approve_intent, allowance 0.02 NEAR", "a.testnet: methods spend,spend_intent, allowance 0.08 NEAR", "plan only"):
            self.assertIn(part, text)

    def test_confirmed_adds_two_keys_and_never_signs_as_the_owner(self):
        code, run = self.run_keys()
        self.assertEqual(code, 0)
        self.assertEqual([c[3] for c in run.calls], ["g.testnet", "a.testnet"])
        for call in run.calls:
            self.assertNotIn("o.testnet", call)
            self.assertNotIn("full-access", " ".join(call))
            self.assertIn("grant-function-call-access", call)
        self.assertIn("OK: only the planned function-call keys exist", "\n".join(self.lines))

    def test_every_key_is_printed_with_methods_and_allowance(self):
        self.run_keys()
        text = "\n".join(self.lines)
        self.assertIn("g.testnet ed25519:K0: function call, receiver v.testnet, methods approve_intent, allowance 0.02 NEAR", text)
        self.assertIn("methods spend,spend_intent, allowance 0.08 NEAR", text)

    def test_an_owner_key_file_stops_mainnet_and_is_only_noted_on_testnet(self):
        for network, owner in (("mainnet", "o.near"), ("testnet", "o.testnet")):
            base = self.home / ".near-credentials" / network
            (base / owner).mkdir(parents=True)
            (base / owner / "ed25519_K.json").write_text("{}")
        main = make_config("mainnet")
        run = Recorder()
        with self.assertRaisesRegex(CallPathError, "owner key"):
            keys.run(main, confirmed=True, runner=run, env={"LEASH_ALLOW_MAINNET": "1"}, out=self.lines.append, fetch=None, home=self.home)
        self.assertEqual(run.calls, [])
        code, run = self.run_keys()
        self.assertEqual((code, len(run.calls)), (0, 2))
        self.assertIn("owner key file is on this machine", "\n".join(self.lines))

    def test_mainnet_needs_the_process_variable(self):
        for env in ({}, {"LEASH_ALLOW_MAINNET": "true"}, {"LEASH_ALLOW_MAINNET": "0"}):
            run = Recorder()
            with self.assertRaisesRegex(CallPathError, "LEASH_ALLOW_MAINNET"):
                keys.run(make_config("mainnet"), confirmed=True, runner=run, env=env, out=self.lines.append, fetch=None, home=self.home)
            self.assertEqual(run.calls, [])

    def test_full_access_unlimited_and_wrong_methods_are_flagged(self):
        cases = {
            "full access": answer("FullAccess", fc(str(2 * 10**22), ["approve_intent"])),
            "no allowance limit": answer(fc(None, ["approve_intent"])),
            "allowance too high": answer(fc(str(3 * 10**22), ["approve_intent"])),
            "extra method": answer(fc(str(2 * 10**22), ["approve_intent", "withdraw"])),
            "other receiver": answer(fc(str(2 * 10**22), ["approve_intent"], receiver="x.testnet")),
        }
        for name, reply in cases.items():
            lines, bad = keys.describe(reply, "g.testnet", "v.testnet", keys.GUARDIAN_METHODS, keys.GUARDIAN_ALLOWANCE)
            self.assertTrue(bad, name)
        self.assertIn("FULL ACCESS", keys.describe(cases["full access"], "g.testnet", "v.testnet", ("approve_intent",), "0.02")[0][0])
        self.assertIn("NO LIMIT", keys.describe(cases["no allowance limit"], "g.testnet", "v.testnet", ("approve_intent",), "0.02")[0][0])
        self.assertFalse(keys.describe(GOOD["g.testnet"], "g.testnet", "v.testnet", ("approve_intent",), "0.02")[1])

    def test_a_full_access_key_fails_on_mainnet_and_only_warns_on_testnet(self):
        bad = {"g.near": answer("FullAccess"), "a.near": answer("FullAccess")}
        code, _ = self.run_keys(cfg=make_config("mainnet"), fetch=lambda a, n: bad[a], env={"LEASH_ALLOW_MAINNET": "1"})
        self.assertEqual(code, 1)
        self.assertIn("FULL ACCESS", "\n".join(self.lines))
        self.lines.clear()
        code, _ = self.run_keys(fetch=lambda a, n: answer("FullAccess"))
        self.assertEqual(code, 0)
        self.assertIn("Remove it", "\n".join(self.lines))

    def test_an_unreadable_key_list_is_an_error(self):
        with self.assertRaisesRegex(CallPathError, "could not be read back"):
            self.run_keys(fetch=lambda a, n: {"error": "x"})


class AccountsScript(unittest.TestCase):
    """scripts/accounts-testnet.sh with a fake `near`: the guardian account is created with the others."""

    def test_the_guardian_account_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            log = Path(tmp) / "near.log"
            near = bin_dir / "near"
            near.write_text(f'#!/usr/bin/env bash\nif [ "$2" = "view-account-summary" ]; then echo "account does not exist"; exit 1; fi\n'
                            f'echo "$@" >> "{log}"\nexit 0\n')
            near.chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", LEASH_PREFIX="t", HOME=tmp)
            proc = subprocess.run(["bash", str(ROOT / "scripts" / "accounts-testnet.sh")], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            created = [line.split()[3] for line in log.read_text().splitlines() if "create-account" in line]
            self.assertEqual(created, ["t-vault.testnet", "t-owner.testnet", "t-agent.testnet", "t-guardian.testnet", "t-payee.testnet", "t-attacker.testnet"])


if __name__ == "__main__":
    unittest.main()
