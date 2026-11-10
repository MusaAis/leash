import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leash_arena.attemptlog import AttemptLog, LogError, public_record
from leash_arena.counters import Counters


class LogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "sub" / "attempts.jsonl"

    def test_the_file_is_created_with_mode_0600(self):
        AttemptLog(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_wider_mode_is_refused(self):
        AttemptLog(self.path)
        os.chmod(self.path, 0o644)
        with self.assertRaises(LogError):
            AttemptLog(self.path)

    def test_append_and_replay_keep_order(self):
        log = AttemptLog(self.path)
        for i in range(3):
            log.append({"id": i, "message": f"m{i}"})
        self.assertEqual([r["id"] for r in log.replay()], [0, 1, 2])

    def test_one_line_per_record_even_with_line_breaks_in_text(self):
        log = AttemptLog(self.path)
        log.append({"message": "a\nb\r\n{\"id\": 99} c"})
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(list(log.replay())), 1)

    def test_private_values_are_replaced(self):
        with mock.patch.dict(os.environ, {"LEASH_ROUTE_RECIPIENT": "0xAbC0000000000000000000000000000000000123"}):
            log = AttemptLog(self.path)
            log.append({"message": "send to 0xabc0000000000000000000000000000000000123", "detail": "x"})
        text = self.path.read_text()
        self.assertNotIn("abc0000000000000000000000000000000000123".lower(), text.lower())
        self.assertIn("<RECIPIENT>", text)

    def test_bad_lines_are_skipped_on_replay(self):
        log = AttemptLog(self.path)
        log.append({"id": 1})
        with open(self.path, "a") as handle:
            handle.write("garbage\n[1,2]\n\n")
        log.append({"id": 2})
        self.assertEqual([r["id"] for r in log.replay()], [1, 2])

    def test_public_record_drops_private_fields(self):
        record = {"id": 1, "visitor": "abc", "tokens": 5, "cost_micro_usd": 1, "detail": "boom",
                  "memo": "m", "message": "hi", "outcome": "refused", "tier": "loose"}
        self.assertEqual(public_record(record), {"id": 1, "message": "hi", "outcome": "refused", "tier": "loose"})


class CounterTests(unittest.TestCase):
    def record(self, outcome, **kw):
        return {"tier": "loose", "outcome": outcome, "fooled": False, **kw}

    def test_each_outcome_is_counted_once(self):
        c = Counters(("fortress", "loose"))
        c.apply(self.record("refused"))
        c.apply(self.record("blocked", fooled=True))
        c.apply(self.record("paid_payee"))
        c.apply(self.record("paid_other", fooled=True, funds_lost_yocto="50000000000000000000000"))
        c.apply(self.record("stopped", fooled=True))
        loose = c.snapshot()["tiers"]["loose"]
        self.assertEqual((loose["attempts"], loose["fooled"], loose["refused"], loose["blocked"]), (5, 3, 1, 1))
        self.assertEqual((loose["paid_payee"], loose["stopped"], loose["funds_lost_count"]), (1, 1, 1))
        self.assertEqual(loose["funds_lost_yocto"], "50000000000000000000000")

    def test_fooled_and_funds_lost_stay_separate(self):
        c = Counters(("loose",))
        c.apply(self.record("blocked", fooled=True))
        snap = c.snapshot()["total"]
        self.assertEqual((snap["fooled"], snap["funds_lost_count"], snap["funds_lost_yocto"]), (1, 0, "0"))

    def test_a_model_error_is_not_an_attempt(self):
        c = Counters(("loose",))
        c.apply(self.record("model_error"))
        row = c.snapshot()["total"]
        self.assertEqual((row["attempts"], row["errors"]), (0, 1))

    def test_a_chain_error_is_an_attempt_and_an_error(self):
        c = Counters(("loose",))
        c.apply(self.record("chain_error", fooled=True))
        row = c.snapshot()["total"]
        self.assertEqual((row["attempts"], row["errors"], row["fooled"], row["funds_lost_count"]), (1, 1, 1, 0))

    def test_unknown_records_are_skipped_and_counted(self):
        c = Counters(("loose",))
        self.assertFalse(c.apply({"tier": "other", "outcome": "refused"}))
        self.assertFalse(c.apply({"tier": "loose", "outcome": "mystery"}))
        self.assertFalse(c.apply({}))
        self.assertEqual(c.skipped, 3)
        self.assertEqual(c.snapshot()["total"]["attempts"], 0)

    def test_totals_add_the_tiers_and_the_network_is_named(self):
        c = Counters(("fortress", "loose"))
        c.apply({"tier": "fortress", "outcome": "refused", "fooled": False})
        c.apply(self.record("refused"))
        snap = c.snapshot()
        self.assertEqual(snap["network"], "testnet")
        self.assertEqual(snap["total"]["attempts"], 2)
        json.dumps(snap)  # plain data only


if __name__ == "__main__":
    unittest.main()
