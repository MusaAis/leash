import json
import os
import tempfile
import unittest
from pathlib import Path

from leash_arena.budget import BudgetExhausted, ModelBudget


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "budget.json"

    def budget(self, cap="4", price="1"):
        return ModelBudget(self.path, cap, price)

    def test_cost_is_rounded_up(self):
        b = self.budget(price="0.5")  # 0.5 USD per million tokens
        self.assertEqual(b.cost_micro(1_000_000), 500_000)
        self.assertEqual(b.cost_micro(1), 1)
        self.assertEqual(b.cost_micro(0), 0)

    def test_reserve_then_settle_records_the_real_cost(self):
        b = self.budget()
        hold = b.reserve(5000)
        cost = b.settle(hold, 1200)
        self.assertEqual(cost, 1200)
        self.assertEqual(b.snapshot()["spent_micro_usd"], 1200)

    def test_the_cap_is_a_hard_stop(self):
        b = self.budget(cap="0.01", price="1")  # 10,000 micro dollars buy 10,000 tokens
        hold = b.reserve(6000)
        with self.assertRaises(BudgetExhausted):
            b.reserve(6000)  # held money counts
        b.settle(hold, 6000)
        with self.assertRaises(BudgetExhausted):
            b.reserve(5000)
        b.reserve(4000)

    def test_an_unused_hold_is_released(self):
        b = self.budget(cap="0.01")
        hold = b.reserve(9000)
        b.settle(hold, 0)
        b.reserve(9000)

    def test_exhausted_flag_follows_spending(self):
        b = self.budget(cap="0.001")
        self.assertFalse(b.exhausted)
        b.settle(b.reserve(10), 1000)
        self.assertTrue(b.exhausted)

    def test_spending_survives_a_restart(self):
        b = self.budget()
        b.settle(b.reserve(100), 700)
        self.assertEqual(self.budget().snapshot()["spent_micro_usd"], 700)

    def test_the_file_has_mode_0600(self):
        b = self.budget()
        b.settle(b.reserve(10), 10)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_damaged_file_stops_all_calls(self):
        for text in ("not json", "{}", '{"spent_micro": -1}', '{"spent_micro": true}', '{"spent_micro": "5"}', "[]"):
            self.path.write_text(text)
            b = self.budget()
            with self.assertRaises(BudgetExhausted, msg=text):
                b.reserve(1)
            self.assertTrue(b.exhausted)

    def test_a_write_failure_stops_further_calls(self):
        b = self.budget()
        self.path.mkdir()  # the file path is now a directory: replace fails
        b.settle(b.reserve(10), 10)
        with self.assertRaises(BudgetExhausted):
            b.reserve(1)

    def test_bad_settings_are_refused(self):
        for cap, price in (("0", "1"), ("-1", "1"), ("x", "1"), ("4", "0"), ("4", "nan"), ("inf", "1")):
            with self.assertRaises(ValueError, msg=f"{cap} {price}"):
                ModelBudget(self.path, cap, price)

    def test_the_file_holds_only_the_spent_amount(self):
        b = self.budget()
        b.settle(b.reserve(10), 10)
        self.assertEqual(json.loads(self.path.read_text()), {"spent_micro": 10})


if __name__ == "__main__":
    unittest.main()
