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
