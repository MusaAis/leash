import io
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from leash_agent import __main__ as cli
from leash_agent.config import EnvFileError, load_env_file


class EnvFile(unittest.TestCase):
    def test_inline_comments_and_quotes(self):
        keys = ["LEASH_T_A", "LEASH_T_B", "LEASH_T_C"]
        for key in keys:
            os.environ.pop(key, None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# comment\nLEASH_T_A=abc # note\nLEASH_T_B="x # y"\nLEASH_T_C=\n')
            load_env_file(path)
        try:
            self.assertEqual(os.environ["LEASH_T_A"], "abc")
            self.assertEqual(os.environ["LEASH_T_B"], "x # y")
            self.assertEqual(os.environ["LEASH_T_C"], "")
        finally:
            for key in keys:
                os.environ.pop(key, None)

    def test_a_repeated_key_is_an_error_that_names_lines_and_no_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("NETWORK=testnet\nA=1\n# NETWORK=old\nNETWORK=mainnet-secret-value\n")
            os.environ.pop("A", None)
            with self.assertRaises(EnvFileError) as ctx:
                load_env_file(path)
            self.assertIn("NETWORK on lines 1, 4", str(ctx.exception))
            self.assertNotIn("secret", str(ctx.exception))
            self.assertNotIn("A", os.environ)  # nothing is loaded from a bad file

    def test_values_already_in_the_environment_still_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("LEASH_T_D=file\n")
            with mock.patch.dict(os.environ, {"LEASH_T_D": "shell"}):
                load_env_file(path)
                self.assertEqual(os.environ["LEASH_T_D"], "shell")

    def test_the_cli_reports_a_bad_env_file_as_a_plain_error(self):
        with mock.patch("leash_agent.config.load_env_file", side_effect=EnvFileError("x repeats a key: K on lines 1, 2.")), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli.main(["intents-tokens"])
        self.assertEqual(code, 1)
        self.assertIn("repeats a key", err.getvalue())

    def test_leash_allow_keys_are_refused_in_the_file_and_no_value_is_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            for line in ("LEASH_ALLOW_MAINNET=yes-secret", "export LEASH_ALLOW_CUSTOM_AI_HOST=1"):
                path.write_text(f"A=1\n{line}\n")
                with self.assertRaises(EnvFileError) as ctx:
                    load_env_file(path)
                self.assertIn("line 2", str(ctx.exception))
                self.assertIn("LEASH_ALLOW_", str(ctx.exception))
                self.assertNotIn("secret", str(ctx.exception))

    def test_export_lines_count_like_plain_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("NETWORK=testnet\nexport NETWORK=mainnet\n")
            with self.assertRaises(EnvFileError) as ctx:
                load_env_file(path)
            self.assertIn("NETWORK on lines 1, 2", str(ctx.exception))
            path.write_text("export LEASH_T_EXPORT=ok\n")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LEASH_T_EXPORT", None)
                load_env_file(path)
                self.assertEqual(os.environ["LEASH_T_EXPORT"], "ok")
                os.environ.pop("LEASH_T_EXPORT", None)


if __name__ == "__main__":
    unittest.main()


class OwnerOverride(unittest.TestCase):
    IMPLICIT = "ab" * 32

    def config(self, network, owner=None):
        from leash_agent.config import Config
        env = {"NETWORK": network, "LEASH_PREFIX": "leash"}
        if owner is not None:
            env["LEASH_OWNER"] = owner
        with mock.patch.dict(os.environ, env, clear=False):
            if owner is None:
                os.environ.pop("LEASH_OWNER", None)
            return Config.from_env()

    def test_the_default_owner_follows_the_prefix_and_the_network(self):
        self.assertEqual(self.config("mainnet").owner, "leash-owner.near")
        self.assertEqual(self.config("testnet").owner, "leash-owner.testnet")

    def test_an_implicit_owner_is_accepted_on_both_networks_and_the_others_keep_their_names(self):
        for network, suffix in (("mainnet", "near"), ("testnet", "testnet")):
            cfg = self.config(network, self.IMPLICIT)
            self.assertEqual(cfg.owner, self.IMPLICIT)
            self.assertEqual((cfg.vault, cfg.guardian, cfg.agent), (f"leash-vault.{suffix}", f"leash-guardian.{suffix}", f"leash-agent.{suffix}"))

    def test_a_bad_owner_is_refused_without_echoing_it(self):
        for bad in ("A.near", "x; echo PWNED", "a b", "-a.near", "a..near", "a", "ab" * 33):
            with self.assertRaises(EnvFileError) as ctx:
                self.config("mainnet", bad)
            self.assertNotIn("PWNED", str(ctx.exception))

    def test_the_mainnet_guard_accepts_an_implicit_owner(self):
        from leash_agent.guard import check
        check("mainnet", [self.IMPLICIT, "leash-vault.near"], {"LEASH_ALLOW_MAINNET": "1"})
        with self.assertRaises(Exception):
            check("mainnet", ["leash-owner.testnet"], {"LEASH_ALLOW_MAINNET": "1"})

