import contextlib
import importlib.util
import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent import chain, keys
from leash_agent.brain import Brain
from leash_agent.config import Config, EnvFileError
from tests.test_chain import make_config

ROOT = Path(__file__).resolve().parents[2]
RECIPIENT = "0x1234567890abcdef1234567890abcdef12345678"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shim_dir(tmp: str, name: str, body: str) -> str:
    path = Path(tmp) / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return tmp


class MakeValuesAreData(unittest.TestCase):
    """Values typed after `make target` reach the recipes as plain arguments, never as shell code."""

    def run_make(self, *args: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            shim_dir(tmp, "python3", 'for a in "$@"; do echo "ARG:$a"; done\n')
            env = {**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"]}
            done = subprocess.run(["make", "-s", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        return done.stdout.splitlines()

    def test_a_hostile_task_is_one_argument(self):
        lines = self.run_make("agent-dry", 'TASK=x"; echo INJECTED; "')
        self.assertIn('ARG:x"; echo INJECTED; "', lines)
        self.assertNotIn("INJECTED", lines)

    def test_hostile_numbers_names_and_lists_stay_arguments(self):
        for target, var in (("attack-llm-testnet", "SAMPLES"), ("intents-dry", "AMOUNT"), ("intents-tokens", "SYMBOL"),
                            ("intents-tokens", "CHAIN"), ("intents-wet-probe", "PROBE_AMOUNT")):
            lines = self.run_make(target, f"{var}=1; echo INJECTED")
            self.assertNotIn("INJECTED", lines, msg=target)
            self.assertIn("ARG:1; echo INJECTED", lines, msg=target)

    def test_model_and_account_lists_are_split_on_spaces_only(self):
        lines = self.run_make("agent-signature", "MODELS=a;echo b*")
        self.assertEqual([x for x in lines if x.startswith("ARG:") and "model" not in x][-2:], ["ARG:a;echo", "ARG:b*"])
        self.assertNotIn("INJECTED", lines)
        lines = self.run_make("wrap-storage", "ACCOUNT=a.near b.near")
        self.assertEqual(lines[-4:], ["ARG:--account", "ARG:a.near", "ARG:--account", "ARG:b.near"])


class TestnetScriptsRefuseOtherNetworks(unittest.TestCase):
    def run_script(self, name: str, **env: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls"
            shim_dir(tmp, "near", f'echo "$@" >> {log}\n')
            full = {**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"], "NETWORK": "mainnet", **env}
            full.pop("LEASH_ALLOW_MAINNET", None) if "LEASH_ALLOW_MAINNET" not in env else None
            done = subprocess.run(["bash", f"scripts/{name}.sh"], cwd=ROOT, env=full, capture_output=True, text=True)
            calls = log.read_text() if log.exists() else ""
        return done.returncode, done.stderr + done.stdout, calls

    def test_demo_deploy_accounts_and_policy_never_call_near_on_mainnet(self):
        for name in ("demo-testnet", "deploy-testnet", "accounts-testnet", "policy-testnet"):
            code, out, calls = self.run_script(name)
            self.assertEqual(code, 1, msg=name)
            self.assertIn("testnet only", out.replace("for test vaults only", "testnet only"), msg=name)
            self.assertEqual(calls, "", msg=name)

    def test_lock_vault_outside_testnet_needs_the_mainnet_flag_for_this_command(self):
        code, out, calls = self.run_script("lock-vault")
        self.assertEqual(code, 1)
        self.assertIn("LEASH_ALLOW_MAINNET=1", out)
        self.assertEqual(calls, "")


class NetworkIsKnown(unittest.TestCase):
    def test_an_unknown_network_is_refused_before_any_account_name_is_built(self):
        with mock.patch.dict("os.environ", {"NETWORK": "localnet"}), mock.patch("leash_agent.config.load_env_file"):
            with self.assertRaises(EnvFileError) as ctx:
                Config.from_env()
        self.assertIn("localnet", str(ctx.exception))

    def main_on_mainnet(self, env: dict, **accounts):
        cfg = make_config()
        cfg = Config(**{**cfg.__dict__, "network": "mainnet", "vault": "v.near", "agent": "a.near", "payee": "p.near", **accounts})
        err = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stderr(err):
            code = cli.main(["attack", "--mode", "compromised"], cfg=cfg, vault=mock.Mock())
        return code, err.getvalue()

    def test_a_paying_command_on_mainnet_also_needs_the_mainnet_flag(self):
        code, err = self.main_on_mainnet({"LEASH_ALLOW_NON_TESTNET": "1"})
        self.assertEqual(code, 1)
        self.assertIn("LEASH_ALLOW_MAINNET", err)

    def test_a_paying_command_on_mainnet_refuses_a_testnet_account(self):
        code, err = self.main_on_mainnet({"LEASH_ALLOW_NON_TESTNET": "1", "LEASH_ALLOW_MAINNET": "1"}, payee="p.testnet")
        self.assertEqual(code, 1)
        self.assertIn("testnet account", err)


class DebugOutputIsScrubbed(unittest.TestCase):
    def test_raw_near_output_hides_the_recipient(self):
        proc = mock.Mock(returncode=0, stdout=f"args {{\"recipient\":\"{RECIPIENT}\"}}", stderr="")
        out = io.StringIO()
        env = {"LEASH_DEBUG": "1", "LEASH_ROUTE_RECIPIENT": RECIPIENT}
        with mock.patch.dict("os.environ", env), mock.patch("leash_agent.chain.subprocess.run", return_value=proc), \
                contextlib.redirect_stdout(out):
            chain.run_near(["near", "x"])
        self.assertNotIn(RECIPIENT, out.getvalue())
        self.assertIn("<RECIPIENT>", out.getvalue())

    def test_the_model_reply_hides_the_recipient(self):
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": f'{{"action":"refuse","reason":"{RECIPIENT}"}}'}}],
                 "usage": {"total_tokens": 5}}
        out = io.StringIO()
        env = {"LEASH_DEBUG": "1", "LEASH_ROUTE_RECIPIENT": RECIPIENT}
        with mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out):
            Brain(make_config(), post=lambda *a: reply).decide("pay")
        self.assertNotIn(RECIPIENT, out.getvalue())
        self.assertIn("<RECIPIENT>", out.getvalue())


class RpcHostIsExact(unittest.TestCase):
    CASES = {
        "https://rpc.example.org": True,
        "http://127.0.0.1:3030": True,
        "http://localhost:3030/path": True,
        "http://localhost.evil.com": False,
        "http://127.0.0.1.evil.com": False,
        "http://localhost@evil.com": False,
        "http://evil.com": False,
        "ftp://localhost": False,
        "https://": False,
        "": False,
    }

    def test_both_copies_of_the_check_agree(self):
        script = load("fak", ROOT / "scripts" / "full_access_keys.py")
        for url, expected in self.CASES.items():
            self.assertEqual(keys.rpc_url_ok(url), expected, msg=url)
            self.assertEqual(script.rpc_url_ok(url), expected, msg=url)


def wasm_with_imports() -> bytes:
    def text(value: str) -> bytes:
        return bytes([len(value)]) + value.encode()

    entries = [
        text("env") + text("a") + b"\x00\x00",  # function, type 0
        text("env") + text("mem") + b"\x02\x01\x01\x02",  # memory, limits with a maximum
        text("env") + text("g") + b"\x03\x7f\x00",  # global
        text("env") + text("t") + b"\x01\x70\x00\x01",  # table, limits without a maximum
    ]
    payload = bytes([len(entries)]) + b"".join(entries)
    types = b"\x01\x04\x01\x60\x00\x00"  # a type section the reader must skip
    return b"\0asm\x01\0\0\0" + types + bytes([2, len(payload)]) + payload


class WasmImports(unittest.TestCase):
    def setUp(self):
        self.mod = load("wasm_imports", ROOT / "scripts" / "wasm_imports.py")

    def test_every_import_kind_is_read_in_order(self):
        self.assertEqual(self.mod.imports(wasm_with_imports()), [("env", "a"), ("env", "mem"), ("env", "g"), ("env", "t")])

    def test_a_module_without_imports_and_a_non_wasm_file(self):
        self.assertEqual(self.mod.imports(b"\0asm\x01\0\0\0"), [])
        with self.assertRaises(ValueError):
            self.mod.imports(b"hello world")

    def test_the_command_prints_the_count_first(self):
        with tempfile.NamedTemporaryFile(suffix=".wasm") as handle:
            handle.write(wasm_with_imports())
            handle.flush()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.mod.main(["x", handle.name]), 0)
        self.assertEqual(out.getvalue().splitlines()[:2], ["4 imports", "env a"])


class BuildInfoNeedsABuild(unittest.TestCase):
    def test_without_the_artifact_it_says_what_to_run(self):
        wasm = ROOT / "contract" / "target" / "near" / "leash.wasm"
        if wasm.exists():
            self.skipTest("an artifact is present")
        done = subprocess.run(["bash", "scripts/build-info.sh"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("make build", done.stderr)


if __name__ == "__main__":
    unittest.main()
