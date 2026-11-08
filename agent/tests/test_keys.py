import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leash_agent import keys
from leash_agent.callpath import CallPathError
from leash_agent.chain import STDOUT_SENTINEL, ChainError
from tests.test_callpath import make_config, value_after

ROOT = Path(__file__).resolve().parents[2]
NEAR = 10**24


def fc(allowance, methods, receiver="v.testnet"):
    return {"FunctionCall": {"allowance": allowance, "receiver_id": receiver, "method_names": methods}}


def answer(*perms):
    keys_ = [{"public_key": f"ed25519:K{i}", "access_key": {"nonce": i, "permission": p}} for i, p in enumerate(perms)]
    return {"result": {"keys": keys_}}


GOOD = {
    "g.testnet": answer(fc(str(7 * 10**22), ["approve_intent"])),
    "a.testnet": answer(fc(str(31 * 10**22), ["spend", "spend_intent"])),
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
        self.assertEqual(value_after(args, "--function-names"), "approve_intent")
        self.assertEqual(value_after(args, "--contract-account-id"), "v.testnet")
        self.assertNotIn("--receiver-account-id", args)
        self.assertNotIn("--method-names", args)
        self.assertEqual(value_after(args, "--allowance"), "0.07 NEAR")
        self.assertEqual(value_after(args, "sign-with-legacy-keychain"), "send")

    def test_the_key_tail_never_leaves_a_question_for_the_cli(self):
        args = keys.add_key_args(make_config(), "g.testnet", keys.GUARDIAN_METHODS, keys.GUARDIAN_ALLOWANCE)
        i = args.index("autogenerate-new-keypair")
        self.assertEqual(args[i + 1:i + 4], ["--signature-scheme", "ed25519", "save-to-legacy-keychain"])
        self.assertEqual(args[-1], "send")

    def test_near_never_reads_a_terminal(self):
        from unittest import mock
        from leash_agent import chain
        done = mock.Mock(stdout="", stderr="", returncode=0)
        with mock.patch.object(chain.subprocess, "run", return_value=done) as run:
            chain.run_near(["near", "--version"])
        self.assertIs(run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_the_agent_key_allows_exactly_spend_and_spend_intent(self):
        args = keys.add_key_args(make_config(), "a.testnet", keys.AGENT_METHODS, keys.AGENT_ALLOWANCE)
        self.assertEqual(value_after(args, "--function-names"), "spend,spend_intent")
        self.assertEqual(value_after(args, "--allowance"), "0.31 NEAR")

    def test_allowances_cover_two_calls_at_the_reserved_cost(self):
        reserved = lambda tgas: 0.001 * tgas + 0.001  # NEAR, measured on testnet
        self.assertGreaterEqual(float(keys.AGENT_ALLOWANCE), 2 * reserved(150))
        self.assertGreaterEqual(float(keys.GUARDIAN_ALLOWANCE), 2 * reserved(30))

    def test_plan_only_sends_nothing_and_lists_every_key(self):
        code, run = self.run_keys(confirmed=False)
        text = "\n".join(self.lines)
        self.assertEqual((code, run.calls), (0, []))
        for part in ("g.testnet: methods approve_intent, allowance 0.07 NEAR", "a.testnet: methods spend,spend_intent, allowance 0.31 NEAR", "plan only"):
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
        self.assertIn("g.testnet ed25519:K0: function call, receiver v.testnet, methods approve_intent, allowance 0.07 NEAR", text)
        self.assertIn("methods spend,spend_intent, allowance 0.31 NEAR", text)

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
            "full access": answer("FullAccess", fc(str(7 * 10**22), ["approve_intent"])),
            "no allowance limit": answer(fc(None, ["approve_intent"])),
            "allowance too high": answer(fc(str(8 * 10**22), ["approve_intent"])),
            "extra method": answer(fc(str(7 * 10**22), ["approve_intent", "withdraw"])),
            "other receiver": answer(fc(str(7 * 10**22), ["approve_intent"], receiver="x.testnet")),
        }
        for name, reply in cases.items():
            lines, bad = keys.describe(reply, "g.testnet", "v.testnet", keys.GUARDIAN_METHODS, keys.GUARDIAN_ALLOWANCE)
            self.assertTrue(bad, name)
        self.assertIn("FULL ACCESS", keys.describe(cases["full access"], "g.testnet", "v.testnet", ("approve_intent",), "0.07")[0][0])
        self.assertIn("NO LIMIT", keys.describe(cases["no allowance limit"], "g.testnet", "v.testnet", ("approve_intent",), "0.07")[0][0])
        self.assertFalse(keys.describe(GOOD["g.testnet"], "g.testnet", "v.testnet", ("approve_intent",), "0.07")[1])

    def test_a_full_access_key_fails_on_mainnet_and_only_warns_on_testnet(self):
        bad = {"g.near": answer("FullAccess"), "a.near": answer("FullAccess")}
        code, _ = self.run_keys(cfg=make_config("mainnet"), confirmed=False, fetch=lambda a, n: bad[a], env={"LEASH_ALLOW_MAINNET": "1"})
        self.assertEqual(code, 1)
        self.assertIn("FULL ACCESS", "\n".join(self.lines))
        self.lines.clear()
        code, _ = self.run_keys(fetch=lambda a, n: answer("FullAccess"))
        self.assertEqual(code, 0)
        self.assertIn("creation key stays on the chain", "\n".join(self.lines))

    def test_an_unreadable_key_list_is_an_error(self):
        with self.assertRaisesRegex(CallPathError, "could not be read back"):
            self.run_keys(fetch=lambda a, n: {"error": "x"})

    def test_plan_only_prints_the_keys_now_on_the_accounts_read_only(self):
        code, run = self.run_keys(confirmed=False)
        self.assertEqual((code, run.calls), (0, []))
        self.assertIn("g.testnet ed25519:K0: function call, receiver v.testnet, methods approve_intent, allowance 0.07 NEAR", "\n".join(self.lines))

    def test_plan_only_survives_an_unreadable_key_list(self):
        def broken(account, network):
            raise keys.ChainError("down")
        code, run = self.run_keys(confirmed=False, fetch=broken)
        self.assertEqual((code, run.calls), (0, []))
        self.assertIn("could not be read", "\n".join(self.lines))


MAIN_ENV = {"LEASH_ALLOW_MAINNET": "1"}
MAIN_GOOD = {
    "g.near": answer(fc(str(7 * 10**22), ["approve_intent"], receiver="v.near")),
    "a.near": answer(fc(str(31 * 10**22), ["spend", "spend_intent"], receiver="v.near")),
}


class Files:
    """Key files in a temporary legacy keychain."""

    def __init__(self, home):
        self.home = Path(home)

    def put(self, network, account, public, name=None, secret="ed25519:SECRETSECRET"):
        folder = self.home / ".near-credentials" / network / account
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or f"{public.split(':')[1]}.json")
        path.write_text(json.dumps({"account_id": account, "public_key": public, "private_key": secret}))
        return path

    def link(self, network, account, public, mode=0o600):
        """The account-level file the CLI opens first."""
        path = self.home / ".near-credentials" / network / f"{account}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"account_id": account, "public_key": public, "private_key": "ed25519:SECRETSECRET"}))
        os.chmod(path, mode)
        return path


class MainnetVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.lines, self.files = Path(self.tmp.name), [], Files(self.tmp.name)
        self.cfg = make_config("mainnet")

    def verify(self, answers=None, env=None, confirmed=False, aside=False, runner=None):
        runner = runner or Recorder()
        code = keys.run(self.cfg, confirmed=confirmed, aside=aside, runner=runner, env=MAIN_ENV if env is None else env, out=self.lines.append,
                        fetch=lambda a, n: (answers or MAIN_GOOD)[a], home=self.home)
        return code, runner

    def function_call_files(self):
        self.files.put("mainnet", "g.near", "ed25519:K0")
        self.files.put("mainnet", "a.near", "ed25519:K0")
        self.files.link("mainnet", "g.near", "ed25519:K0")
        self.files.link("mainnet", "a.near", "ed25519:K0")

    def test_function_call_keys_only_passes_and_changes_nothing(self):
        self.function_call_files()
        code, run = self.verify()
        self.assertEqual((code, run.calls), (0, []))
        self.assertIn("OK: this machine holds only function-call keys", "\n".join(self.lines))

    def test_mainnet_never_adds_keys_or_moves_files(self):
        self.function_call_files()
        for kw in ({"confirmed": True}, {"aside": True}):
            run = Recorder()
            with self.assertRaisesRegex(CallPathError, "only verifies"):
                self.verify(runner=run, **kw)
            self.assertEqual(run.calls, [])

    def test_a_full_access_key_file_on_the_machine_fails_even_if_the_key_is_listed(self):
        self.function_call_files()
        self.files.put("mainnet", "g.near", "ed25519:FULL")
        answers = dict(MAIN_GOOD, **{"g.near": answer(fc(str(7 * 10**22), ["approve_intent"], receiver="v.near"), "FullAccess")})
        answers["g.near"]["result"]["keys"][1]["public_key"] = "ed25519:FULL"
        code, _ = self.verify(answers, env=dict(MAIN_ENV, LEASH_KNOWN_FULL_ACCESS_KEYS="ed25519:FULL"))
        self.assertEqual(code, 1)
        self.assertIn("FULL-ACCESS KEY FILE on this machine", "\n".join(self.lines))

    def test_an_unlisted_full_access_key_on_the_chain_fails_and_a_listed_one_passes(self):
        self.function_call_files()
        answers = dict(MAIN_GOOD, **{"a.near": answer(fc(str(31 * 10**22), ["spend", "spend_intent"], receiver="v.near"), "FullAccess")})
        answers["a.near"]["result"]["keys"][1]["public_key"] = "ed25519:OWNERDEVICE"
        code, _ = self.verify(answers)
        self.assertEqual(code, 1)
        self.assertIn("FULL ACCESS", "\n".join(self.lines))
        self.lines.clear()
        for listed in ("ed25519:OWNERDEVICE", "ed25519:X, ed25519:OWNERDEVICE"):
            code, _ = self.verify(answers, env=dict(MAIN_ENV, LEASH_KNOWN_FULL_ACCESS_KEYS=listed))
            self.assertEqual(code, 0, listed)
        self.assertIn("(listed in LEASH_KNOWN_FULL_ACCESS_KEYS)", "\n".join(self.lines))

    def test_a_key_listed_for_another_value_does_not_pass(self):
        self.function_call_files()
        answers = dict(MAIN_GOOD, **{"a.near": answer(fc(str(31 * 10**22), ["spend", "spend_intent"], receiver="v.near"), "FullAccess")})
        answers["a.near"]["result"]["keys"][1]["public_key"] = "ed25519:OWNERDEVICE"
        for listed in ("", "ed25519:OTHER", "OWNERDEVICE", "ed25519:OWNERDEVICE2"):
            code, _ = self.verify(answers, env=dict(MAIN_ENV, LEASH_KNOWN_FULL_ACCESS_KEYS=listed))
            self.assertEqual(code, 1, listed)

    def test_a_key_only_in_the_account_folder_fails_because_the_cli_opens_the_account_file_first(self):
        self.files.put("mainnet", "g.near", "ed25519:K0")
        self.files.put("mainnet", "a.near", "ed25519:K0")
        code, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("g.near.json is missing or unreadable", "\n".join(self.lines))

    def test_the_account_file_must_hold_the_planned_key_and_be_private(self):
        self.function_call_files()
        self.files.link("mainnet", "g.near", "ed25519:K0", mode=0o644)
        code, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("g.near.json is readable by others", "\n".join(self.lines))
        self.lines.clear()
        self.files.link("mainnet", "g.near", "ed25519:K0")
        self.files.link("mainnet", "a.near", "ed25519:NOTONCHAIN")
        code, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("a.near.json does not hold a function-call key", "\n".join(self.lines))

    def test_an_empty_key_list_is_unknown_never_ok(self):
        empty = {"result": {"keys": []}}
        with self.assertRaisesRegex(ChainError, "empty, which is unknown"):
            keys.describe(empty, "g.testnet", "v.testnet", ("approve_intent",), "0.07")
        self.function_call_files()
        with self.assertRaises(CallPathError):
            self.verify({"g.near": empty, "a.near": empty})

    def test_the_funding_of_the_two_accounts_is_above_the_reserved_cost(self):
        self.assertGreater(float(keys.GUARDIAN_FUNDING_MAINNET), 0.031)
        self.assertGreater(float(keys.AGENT_FUNDING_MAINNET), 0.151)
        self.function_call_files()
        self.verify()
        self.assertIn("guardian 0.08 NEAR, agent 0.25 NEAR", "\n".join(self.lines))

    def test_no_function_call_key_file_means_the_machine_cannot_sign(self):
        self.files.put("mainnet", "g.near", "ed25519:K0")
        code, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("a.near: no function-call key file on this machine", "\n".join(self.lines))

    def test_a_loose_allowance_or_wrong_methods_fail(self):
        self.function_call_files()
        loose = dict(MAIN_GOOD, **{"g.near": answer(fc(str(10 * 10**22), ["approve_intent"], receiver="v.near"))})
        self.assertEqual(self.verify(loose)[0], 1)
        wrong = dict(MAIN_GOOD, **{"a.near": answer(fc(str(31 * 10**22), ["spend", "spend_intent", "withdraw"], receiver="v.near"))})
        self.assertEqual(self.verify(wrong)[0], 1)

    def test_secrets_in_key_files_are_never_printed(self):
        self.function_call_files()
        self.files.put("mainnet", "a.near", "ed25519:FULL")
        self.verify()
        self.assertNotIn("SECRETSECRET", "\n".join(self.lines))

    def test_without_the_mainnet_variable_nothing_is_read(self):
        with self.assertRaisesRegex(CallPathError, "LEASH_ALLOW_MAINNET"):
            self.verify(env={})


class Aside(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.lines, self.files = Path(self.tmp.name), [], Files(self.tmp.name)
        self.cfg = make_config("testnet")
        # on the chain after the add: the creation key (full access) and the new function-call key
        self.answers = {
            "g.testnet": answer("FullAccess", fc(str(7 * 10**22), ["approve_intent"])),
            "a.testnet": answer("FullAccess", fc(str(31 * 10**22), ["spend", "spend_intent"])),
        }
        for account in self.answers:
            self.answers[account]["result"]["keys"][0]["public_key"] = "ed25519:CREATION"
            self.files.put("testnet", account, "ed25519:CREATION")
            self.files.put("testnet", account, "ed25519:K1")
            self.answers[account]["result"]["keys"][1]["public_key"] = "ed25519:K1"

    def run_aside(self, aside=True):
        return keys.run(self.cfg, confirmed=True, aside=aside, runner=Recorder(), out=self.lines.append,
                        fetch=lambda a, n: self.answers[a], home=self.home)

    def test_creation_key_files_move_aside_and_function_call_key_files_stay(self):
        self.assertEqual(self.run_aside(), 0)
        for account in self.answers:
            kept = {public for _, public in keys.local_key_files(account, "testnet", self.home)}
            self.assertEqual(kept, {"ed25519:K1"})
            moved = self.home / ".near-credentials-aside" / "testnet" / account
            self.assertEqual([p.name for p in moved.iterdir()], ["CREATION.json"])
            self.assertEqual(oct(moved.stat().st_mode & 0o777), "0o700")

    def test_the_function_call_key_file_is_linked_where_the_cli_looks(self):
        self.run_aside()
        for account in self.answers:
            link = self.home / ".near-credentials" / "testnet" / f"{account}.json"
            self.assertEqual(json.loads(link.read_text())["public_key"], "ed25519:K1")
            self.assertEqual(oct(link.stat().st_mode & 0o777), "0o600")

    def test_the_linked_key_file_is_private_from_its_creation(self):
        old = os.umask(0)
        try:
            with mock.patch("leash_agent.keys.os.chmod"):  # the mode must come from the open call itself
                keys.link_account_file(self.cfg, {"g.testnet": self.answers["g.testnet"]}, self.home)
        finally:
            os.umask(old)
        link = self.home / ".near-credentials" / "testnet" / "g.testnet.json"
        self.assertEqual(oct(link.stat().st_mode & 0o777), "0o600")

    def test_an_existing_account_file_is_never_overwritten(self):
        link = self.home / ".near-credentials" / "testnet" / "g.testnet.json"
        link.write_text("keep")
        keys.link_account_file(self.cfg, {"g.testnet": self.answers["g.testnet"]}, self.home)
        self.assertEqual(link.read_text(), "keep")

    def test_a_skipped_link_says_why(self):
        # two function-call key files: not exactly one
        self.files.put("testnet", "g.testnet", "ed25519:K1", name="copy.json")
        # an account file that holds a dead key stays in place and is named
        self.files.link("testnet", "a.testnet", "ed25519:DEAD")
        self.run_aside()
        text = "\n".join(self.lines)
        self.assertIn("g.testnet: 2 function-call key files found, not exactly one; g.testnet.json was not written", text)
        self.assertIn("a.testnet: a.testnet.json already exists and does not hold a function-call key of the account; left in place", text)

    def test_an_account_file_that_already_holds_the_key_is_reported_as_in_place(self):
        self.files.link("testnet", "g.testnet", "ed25519:K1")
        self.run_aside()
        self.assertIn("g.testnet: g.testnet.json already exists and holds a function-call key of the account; left in place", "\n".join(self.lines))

    def test_nothing_is_linked_without_the_aside_flag(self):
        self.run_aside(aside=False)
        self.assertFalse((self.home / ".near-credentials" / "testnet" / "g.testnet.json").exists())

    def test_without_the_flag_nothing_moves(self):
        self.run_aside(aside=False)
        self.assertFalse((self.home / ".near-credentials-aside").exists())
        self.assertEqual(len(keys.local_key_files("g.testnet", "testnet", self.home)), 2)

    def test_nothing_moves_if_no_function_call_key_file_would_remain(self):
        (self.home / ".near-credentials" / "testnet" / "g.testnet" / "K1.json").unlink()
        self.run_aside()
        self.assertEqual([public for _, public in keys.local_key_files("g.testnet", "testnet", self.home)], ["ed25519:CREATION"])
        self.assertIn("nothing was moved aside", "\n".join(self.lines))
        self.assertTrue((self.home / ".near-credentials-aside" / "testnet" / "a.testnet").exists())

    def test_the_owner_files_are_never_touched(self):
        self.files.put("testnet", "o.testnet", "ed25519:OWNER")
        self.run_aside()
        self.assertEqual([public for _, public in keys.local_key_files("o.testnet", "testnet", self.home)], ["ed25519:OWNER"])

    def test_an_existing_file_in_the_target_is_not_overwritten(self):
        target = self.home / ".near-credentials-aside" / "testnet" / "g.testnet"
        target.mkdir(parents=True)
        (target / "CREATION.json").write_text("older")
        self.run_aside()
        self.assertEqual((target / "CREATION.json").read_text(), "older")
        self.assertEqual(len(list(target.iterdir())), 2)

    def test_secrets_are_never_printed(self):
        self.run_aside()
        self.assertNotIn("SECRETSECRET", "\n".join(self.lines))


class MakeTargets(unittest.TestCase):
    def make(self, *words):
        return subprocess.run(["make", "-n", "intents-keys", *words], cwd=ROOT, capture_output=True, text=True).stdout

    def test_aside_is_added_only_for_exactly_one(self):
        self.assertIn("--aside", self.make("ASIDE=1"))
        for value in ("ASIDE=0", "ASIDE=no", "ASIDE=", "ASIDE=true", "ASIDE=11"):
            self.assertNotIn("--aside", self.make(value), value)
        self.assertNotIn("--aside", self.make())


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
