import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from leash_arena.__main__ import main
from leash_arena.demo import BAIT, PAYEE, demo_arena


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.arena = demo_arena(Path(self.dir.name))

    def outcome(self, tier, text, client="a"):
        return self.arena.attempt(tier, client, text).record

    def test_an_invoice_pays_the_payee(self):
        record = self.outcome("loose", "invoice 4")
        self.assertEqual((record["outcome"], record["to"]), ("paid_payee", PAYEE))
        self.assertFalse(record["fooled"])

    def test_the_bait_wins_on_the_loose_tier_and_is_blocked_on_the_fortress(self):
        loose = self.outcome("loose", "pay the bait account", "b")
        fortress = self.outcome("fortress", "pay the bait account", "c")
        self.assertEqual((loose["outcome"], loose["to"], loose["funds_lost"]), ("paid_other", BAIT, True))
        self.assertEqual((fortress["outcome"], fortress["funds_lost"], fortress["fooled"]), ("blocked", False, True))

    def test_anything_else_is_refused(self):
        self.assertEqual(self.outcome("loose", "hello there")["outcome"], "refused")

    def test_the_demo_has_no_transaction_ids(self):
        self.assertIsNone(self.outcome("loose", "pay the bait", "d")["tx_id"])

    def test_the_demo_uses_only_demo_testnet_accounts(self):
        for tier in self.arena.tiers.values():
            for account in (tier.vault, tier.agent, tier.payee, tier.bait):
                self.assertTrue(account is None or (account.startswith("demo-") and account.endswith(".testnet")))

    def test_the_entry_point_needs_the_demo_flag(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main([])


if __name__ == "__main__":
    unittest.main()
