import http.server
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("full_access_keys", ROOT / "scripts" / "full_access_keys.py")
fak = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fak)


def answer(*perms):
    keys = [{"public_key": f"ed25519:K{i}", "access_key": {"nonce": i, "permission": p}} for i, p in enumerate(perms)]
    return {"jsonrpc": "2.0", "id": "leash", "result": {"keys": keys, "block_height": 1}}


FUNCTION_CALL = {"FunctionCall": {"allowance": "1", "receiver_id": "v.testnet", "method_names": ["pause_all"]}}


_spec_docs = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec_docs)
_spec_docs.loader.exec_module(check_docs)


class VersionNames(unittest.TestCase):
    def test_a_fix_suffix_is_understood_and_an_unreleased_heading_is_not_a_version(self):
        import re
        self.assertEqual(re.search(check_docs.VERSION_LINE, "- Version: v0.3.0-fix1 (x)").group(1), "v0.3.0-fix1")
        self.assertEqual(re.search(check_docs.VERSION_LINE, "- Version: v0.3.0").group(1), "v0.3.0")
        log = "## [Unreleased]\n## [0.3.0-fix1] - d\n## [0.3.0] - d\n"
        self.assertEqual(re.search(check_docs.CHANGELOG_TOP, log, re.M).group(1), "0.3.0-fix1")
        self.assertIsNone(re.search(check_docs.CHANGELOG_TOP, "## [Unreleased]\n", re.M))


class RiskyKeys(unittest.TestCase):
    def test_only_non_function_call_keys_are_listed(self):
        self.assertEqual(fak.risky_keys(answer("FullAccess", FUNCTION_CALL, "FullAccess")), ["ed25519:K0", "ed25519:K2"])
        self.assertEqual(fak.risky_keys(answer(FUNCTION_CALL)), [])
        self.assertEqual(fak.risky_keys(answer()), [])

    def test_an_unknown_permission_is_never_treated_as_safe(self):
        self.assertEqual(fak.risky_keys(answer({"GasKey": {}}, "Mystery")), ["ed25519:K0", "ed25519:K1"])

    def test_unreadable_answers_raise_instead_of_passing(self):
        for bad in (None, [], {}, {"error": {"name": "UNKNOWN_ACCOUNT"}}, {"result": {}}, {"result": {"keys": "x"}},
                    {"result": {"keys": [{"public_key": "k"}]}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                fak.risky_keys(bad)

    def test_non_https_endpoint_is_refused(self):
        old = os.environ.get("LEASH_RPC_URL")
        os.environ["LEASH_RPC_URL"] = "http://rpc.example"
        try:
            with self.assertRaises(ValueError):
                fak.fetch("v.testnet", "testnet")
        finally:
            os.environ.pop("LEASH_RPC_URL")
            if old is not None:
                os.environ["LEASH_RPC_URL"] = old


class _FakeRpc(http.server.BaseHTTPRequestHandler):
    flag = ""  # path of a file; once it exists the full-access key is "deleted"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        deleted = Path(type(self).flag).exists()
        body = json.dumps(answer(FUNCTION_CALL) if deleted else answer("FullAccess", FUNCTION_CALL)).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LockVaultScript(unittest.TestCase):
    """Runs scripts/lock-vault.sh with a fake `near` and a fake RPC. Nothing real is touched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.flag = tmp / "deleted"
        _FakeRpc.flag = str(self.flag)
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _FakeRpc)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        (tmp / "bin").mkdir()
        near = tmp / "bin" / "near"
        near.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{tmp}/near.log"\n'
                        f'[ "$2" = "delete-keys" ] && [ "$NEAR_FAKE_DELETE" = "1" ] && touch "{self.flag}"\nexit 0\n')
        near.chmod(0o755)
        self.log = tmp / "near.log"

    def run_script(self, stdin, delete=True):
        env = dict(os.environ, PATH=f"{self.tmp.name}/bin:{os.environ['PATH']}", LEASH_PREFIX="t", LEASH_LOCK_WAIT="0",
                   LEASH_RPC_URL=f"http://127.0.0.1:{self.server.server_port}",
                   NEAR_FAKE_DELETE="1" if delete else "0")
        return subprocess.run(["bash", str(ROOT / "scripts" / "lock-vault.sh")], input=stdin, text=True,
                              capture_output=True, env=env, timeout=60)

    def test_deleting_the_key_is_verified_from_the_rpc(self):
        proc = self.run_script("ed25519:K0\nt-vault.testnet\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Verified: no full-access key remains", proc.stdout)
        self.assertIn("delete-keys", self.log.read_text())

    def test_a_key_that_is_not_full_access_is_refused_before_anything_is_sent(self):
        proc = self.run_script("ed25519:K1\nt-vault.testnet\n")  # K1 is the function-call key
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not a full-access key", proc.stdout)
        self.assertFalse(self.log.exists())

    def test_a_wrong_confirmation_cancels(self):
        proc = self.run_script("ed25519:K0\nwrong\n")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_if_the_key_is_still_there_the_script_fails(self):
        proc = self.run_script("ed25519:K0\nt-vault.testnet\n", delete=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("still has a full-access key", proc.stdout)

    def test_an_unreadable_rpc_stops_the_script_without_changes(self):
        env = dict(os.environ, PATH=f"{self.tmp.name}/bin:{os.environ['PATH']}", LEASH_PREFIX="t",
                   LEASH_RPC_URL="http://127.0.0.1:1")
        proc = subprocess.run(["bash", str(ROOT / "scripts" / "lock-vault.sh")], input="ed25519:K0\nt-vault.testnet\n",
                              text=True, capture_output=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Could not read the key list", proc.stderr)
        self.assertFalse(self.log.exists())


class ConfigAccountNames(unittest.TestCase):
    def names(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("NETWORK", "LEASH_PREFIX")}
        out = subprocess.run(
            ["bash", "-c", 'source scripts/config.sh; echo "$VAULT $OWNER $AGENT $GUARDIAN $PAYEE $ATTACKER $GHOST"'],
            cwd=ROOT, env={**base, **env}, capture_output=True, text=True, check=True,
        )
        return out.stdout.split()

    def test_mainnet_names_end_in_near(self):
        names = self.names(NETWORK="mainnet", LEASH_PREFIX="leash")
        self.assertEqual(names[0], "leash-vault.near")
        self.assertTrue(all(n.endswith(".near") for n in names), names)

    def test_an_unknown_network_is_refused(self):
        out = subprocess.run(
            ["bash", "-c", "source scripts/config.sh; echo reached"],
            cwd=ROOT, env={**os.environ, "NETWORK": "foo"}, capture_output=True, text=True,
        )
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("reached", out.stdout)
        self.assertIn("NETWORK must be testnet or mainnet", out.stderr)

    def test_testnet_scripts_refuse_mainnet_even_with_the_mainnet_flag(self):
        for script in ("accounts-testnet.sh", "deploy-testnet.sh", "demo-testnet.sh", "policy-testnet.sh"):
            for flag in ({}, {"LEASH_ALLOW_MAINNET": "1"}):
                env = {**os.environ, "NETWORK": "mainnet", "LEASH_PREFIX": "leash", **flag}
                out = subprocess.run(["bash", "scripts/" + script], cwd=ROOT, env=env, capture_output=True, text=True)
                with self.subTest(script=script, flag=bool(flag)):
                    self.assertEqual(out.returncode, 1)
                    self.assertIn("testnet only", out.stderr)

    def test_testnet_is_the_default_and_names_end_in_testnet(self):
        names = self.names(LEASH_PREFIX="demo")
        self.assertEqual(names[0], "demo-vault.testnet")
        self.assertTrue(all(n.endswith(".testnet") for n in names), names)


class AccountScriptKeyScheme(unittest.TestCase):
    def test_account_creation_names_the_signature_scheme(self):
        text = (ROOT / "scripts" / "accounts-testnet.sh").read_text()
        self.assertIn("autogenerate-new-keypair --signature-scheme ed25519 save-to-legacy-keychain", text)


class ReceivingAccountsAvoidTheFaucet(unittest.TestCase):
    """Runs scripts/accounts-testnet.sh with a fake `near` that logs its arguments."""

    def run_script(self, existing):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bin_dir = Path(tmp.name) / "bin"
        bin_dir.mkdir()
        log = Path(tmp.name) / "calls.txt"
        shim = bin_dir / "near"
        shim.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{log}"\n'
            'if [ "$2" = view-account-summary ]; then\n'
            f'  case " {existing} " in *" $3 "*) exit 0 ;; esac\n'
            '  echo "account $3 does not exist"; exit 1\n'
            "fi\n"
            "exit 0\n"
        )
        shim.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=tmp.name, LEASH_PREFIX="z")
        env.pop("NETWORK", None)
        out = subprocess.run(["bash", "scripts/accounts-testnet.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
        calls = log.read_text().splitlines() if log.exists() else []
        return out, calls

    def test_payee_and_attacker_are_created_by_the_owner_not_the_faucet(self):
        out, calls = self.run_script("z-vault.testnet z-owner.testnet z-agent.testnet z-guardian.testnet")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse([c for c in calls if "sponsor-by-faucet-service" in c], calls)
        for name in ("z-payee.testnet", "z-attacker.testnet"):
            created = [c for c in calls if "create-account fund-myself " + name in c]
            self.assertEqual(len(created), 1, calls)
            self.assertIn("sign-as z-owner.testnet", created[0])
            self.assertIn("0.1 NEAR", created[0])
            self.assertIn("--signature-scheme ed25519", created[0])

    def test_the_other_accounts_still_use_the_faucet(self):
        out, calls = self.run_script("")
        faucet = [c for c in calls if "sponsor-by-faucet-service" in c]
        self.assertEqual(len(faucet), 4, calls)
        self.assertFalse([c for c in faucet if "payee" in c or "attacker" in c], faucet)


class SmokeScript(unittest.TestCase):
    def test_smoke_deploys_the_file_and_sets_a_test_route(self):
        text = (ROOT / "scripts" / "smoke-testnet.sh").read_text()
        self.assertNotIn("make deploy-testnet", text)
        self.assertIn("scripts/deploy-testnet.sh", text)
        self.assertIn("LEASH_ROUTE_RECIPIENT", text)
        self.assertIn("scripts/vault-has-code.sh", text)
        self.assertNotIn("grep -qi", text)


class VaultHasCode(unittest.TestCase):
    """Runs scripts/vault-has-code.sh with a fake `near` that prints a canned account summary."""

    SHA = "18f112959fc2a6ec4a83489803589c83872c43564e96cd94ac70df1f1113dff0"
    B58 = "2gMzTHTr2TwtwAaHsXZbKH9nBLi2ZfU9HLMVHG82B8KZ"

    def run_script(self, answers, tries="3", sha=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bin_dir = Path(tmp.name) / "bin"
        bin_dir.mkdir()
        for i, text in enumerate(answers):
            (Path(tmp.name) / f"a{i}").write_text(text)
        counter = Path(tmp.name) / "n"
        shim = bin_dir / "near"
        shim.write_text(
            "#!/bin/sh\n"
            f'n=$(cat "{counter}" 2>/dev/null || echo 0)\n'
            f'last={len(answers) - 1}\n'
            '[ "$n" -gt "$last" ] && n=$last\n'
            f'cat "{tmp.name}/a$n"\n'
            f'echo $((n + 1)) > "{counter}"\n'
        )
        shim.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", LEASH_CODE_WAIT="0")
        env.pop("NETWORK", None)
        return subprocess.run(
            ["bash", "scripts/vault-has-code.sh", "v.testnet", sha or self.SHA, tries],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )

    def test_hex_hash_matches(self):
        out = self.run_script([f"Contract (SHA-256 checksum hex): {self.SHA}\n"])
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_base58_hash_matches(self):
        out = self.run_script([f"Contract hash: {self.B58}\n"])
        self.assertEqual(out.returncode, 0, out.stderr)

    ZERO_SHA = "0018f112959fc2a6ec4a83489803589c83872c43564e96cd94ac70df1f1113df"
    ZERO_B58 = "1P4PzAAi1nXZcP7Bcv9HqYkgEfuLqdovzApgwSBCbU2"

    def test_a_hash_with_a_leading_zero_byte_matches_in_base58(self):
        out = self.run_script([f"Contract hash: {self.ZERO_B58}\n"], sha=self.ZERO_SHA)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_base58_form_is_matched_case_sensitively(self):
        out = self.run_script([f"Contract hash: {self.B58.lower()}\n"], tries="2")
        self.assertEqual(out.returncode, 1)

    def test_a_lagging_read_is_retried(self):
        out = self.run_script(["Contract: none\n", f"Contract hash: {self.SHA}\n"])
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_another_hash_fails_and_shows_the_lines(self):
        out = self.run_script(["Contract hash: FwEhAR96xHdsMmMrHM4vqNJgKNoH66VapxMY19AeXzne\n"], tries="2")
        self.assertEqual(out.returncode, 1)
        self.assertIn("does not match", out.stderr)
        self.assertIn("FwEhAR96", out.stderr)

    def test_a_bad_sha_is_refused(self):
        out = subprocess.run(["bash", "scripts/vault-has-code.sh", "v.testnet", "nothex"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
