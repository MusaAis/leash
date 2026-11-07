"""Checks the replay script and the golden vector against each other."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def repo_root() -> str:
    path = HERE
    while not os.path.isfile(os.path.join(path, "contract", "testdata", "chain_golden.json")):
        parent = os.path.dirname(path)
        if parent == path:
            raise AssertionError("contract/testdata/chain_golden.json not found")
        path = parent
    return path


ROOT = repo_root()
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen_chain_golden  # noqa: E402
import replay_head  # noqa: E402

GOLDEN_PATH = os.path.join(ROOT, "contract", "testdata", "chain_golden.json")
SCRIPT = os.path.join(ROOT, "scripts", "replay_head.py")


def load() -> dict:
    with open(GOLDEN_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["steps"], "golden file is empty"
    return data


class ReplayHeadTests(unittest.TestCase):
    def test_golden_file_equals_the_generator_output(self):
        self.assertEqual(load(), gen_chain_golden.build())

    def test_every_step_matches_and_all_four_outcomes_are_present(self):
        data = load()
        names = [row["outcome"] for row in data["steps"]]
        self.assertEqual(
            names, ["spend_allowed", "spend_denied", "intent_sent", "intent_denied"]
        )
        lines = []
        for row in data["steps"]:
            lines.append(row["line"])
            self.assertEqual(replay_head.head_of(lines), row["head"])
            self.assertTrue(replay_head.verify(lines, row["head"]))
        self.assertEqual(replay_head.head_of([]), data["genesis"])

    def test_altered_missing_reordered_and_extra_lines_are_broken(self):
        data = load()
        lines = [row["line"] for row in data["steps"]]
        head = data["steps"][-1]["head"]
        tampered = data["tampered"]["lines"]
        self.assertNotEqual(tampered, lines)
        self.assertEqual(replay_head.head_of(tampered), data["tampered"]["head"])
        for bad in (tampered, lines[:-1], lines[::-1], lines + [""], lines + lines[:1]):
            self.assertFalse(replay_head.verify(bad, head))
        self.assertFalse(replay_head.verify(lines, head.upper()))
        self.assertFalse(replay_head.verify(lines, head[:-1]))

    def test_prefixed_lines_hash_the_same_text(self):
        data = load()
        lines = [row["line"] for row in data["steps"]]
        prefixed = ["EVENT_JSON:" + line for line in lines]
        self.assertEqual(replay_head.head_of(prefixed), replay_head.head_of(lines))

    def test_command_prints_ok_or_broken_with_exit_codes(self):
        data = load()
        head = data["steps"][-1]["head"]
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "events.txt")
            with open(good, "w", encoding="utf-8") as handle:
                handle.write("\n".join(row["line"] for row in data["steps"]) + "\n")
            ok = subprocess.run([sys.executable, SCRIPT, good, head], capture_output=True, text=True)
            self.assertEqual((ok.stdout.strip(), ok.returncode), ("chain OK", 0))
            bad = subprocess.run([sys.executable, SCRIPT, good, "0" * 64], capture_output=True, text=True)
            self.assertEqual((bad.stdout.strip(), bad.returncode), ("chain BROKEN", 1))
            usage = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
            self.assertEqual(usage.returncode, 2)


if __name__ == "__main__":
    unittest.main()
