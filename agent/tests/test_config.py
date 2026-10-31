import os
import tempfile
import unittest
from pathlib import Path

from leash_agent.config import load_env_file


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


if __name__ == "__main__":
    unittest.main()
