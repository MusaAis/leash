"""Each docs check fails on a bad fixture and passes on a good one."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_docs", Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py")
cd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cd)

SOURCE = (
    "impl V {\n    pub fn new(a: u8) {}\n    pub fn spend(&mut self) {}\n    pub fn spend_intent(&mut self) {\n"
    '        emit(\n            "spend_allowed",\n            x);\n        emit("spend_denied", y);\n    }\n}\n#[cfg(test)]\nmod t { pub fn helper() {} }\n'
)
SHA = "a" * 64


class DocsChecks(unittest.TestCase):
    def test_stale_phrases_fail_one_by_one(self):
        for bad in ("not released", "work in progress", "not deployed", "a placeholder", "to be measured", "no LEASH contract",
                    "as reported by the API", "swap (reported)", "reported, not shown", "the owner reports", "the owner's report",
                    "(the owner's statement)", "it is inferred", "is unverified", "not yet verified", "the rehearsal records"):
            self.assertTrue(cd.stale_phrases("d.md", bad), bad)

    def test_stale_phrases_pass(self):
        for good in ("1Click reported the swap as SUCCESS.", "Every pending approval is revoked.", "the owner's policy"):
            self.assertEqual(cd.stale_phrases("d.md", good), [], good)

    def test_private_shapes_fail_one_by_one(self):
        for bad in ("LE" + "AD decided", "the Re" + "viewer", "Bui" + "lder notes", "Advi" + "ser says", "see Re" + "view 19",
                    "finding F" + "-001", "decision G" + "-1", "item P16" + "-3", "R17" + "-8", "Pat" + "ch 6 adds", "Mu" + "sa said"):
            self.assertTrue(cd.private_shapes("d.md", bad), bad)

    def test_private_shapes_pass(self):
        for good in ("Near Builders and a leader", "github.com/Mu" + "saAis/leash", "SHA-256 and UTF-8", "leashmusa-vault.testnet"):
            self.assertEqual(cd.private_shapes("d.md", good), [], good)

    def test_interface_needs_the_exact_name(self):
        self.assertEqual(cd.interface_gaps(SOURCE, "`new(a)` `spend(x)` `spend_intent(y)`"), [])
        self.assertEqual(len(cd.interface_gaps(SOURCE, "`new(a)` `spend_intent(y)`")), 1)
        self.assertEqual(len(cd.interface_gaps(SOURCE, "`new(a)` `spend(x)` `spend_intents`")), 1)

    def test_event_list(self):
        self.assertEqual(cd.event_gaps(SOURCE, "Events: `spend_allowed`, `spend_denied`."), [])
        self.assertEqual(cd.event_gaps(SOURCE, "Events: `spend_allowed`."), ["README event list has no `spend_denied`"])
        self.assertEqual(len(cd.event_gaps(SOURCE, "no events line")), 2)

    def test_badge(self):
        good = 'badge/network-NEAR%20mainnet%20and%20testnet-informational"'
        self.assertEqual(cd.badge_problems(good, True), [])
        self.assertTrue(cd.badge_problems('badge/network-NEAR%20testnet-informational"', True))
        self.assertEqual(cd.badge_problems('badge/network-NEAR%20testnet-informational"', False), [])
        self.assertTrue(cd.badge_problems("none", False))

    def test_pin(self):
        toml = 'near-sdk = "=5.28.3"\nnear-sdk = { version = "=5.28.3", features = ["x"] }\n'
        self.assertEqual(cd.pin_problems(toml, "near-sdk `=5.28.3`"), [])
        self.assertTrue(cd.pin_problems(toml, "near-sdk 5.30.0"))
        self.assertTrue(cd.pin_problems('near-sdk = "5.22"', "5.22"))

    def test_wasm_facts(self):
        build = "| sha256 | `%s` |\n| Code hash on chain (base58) | `Abc123` |\n" % SHA
        self.assertEqual(cd.wasm_problems(build, "%s Abc123" % SHA), [])
        self.assertEqual(len(cd.wasm_problems(build, "%s" % SHA)), 1)
        self.assertEqual(len(cd.wasm_problems("none", "x")), 2)

    def test_dated_balance_in_prose(self):
        self.assertTrue(cd.snapshot_problems("s.md", "Available balance on 2026-11-06: 0.2874 NEAR."))
        self.assertEqual(cd.snapshot_problems("s.md", "Read the balance with `get_available_balance`."), [])
        self.assertEqual(cd.snapshot_problems("s.md", "The swap ran on 2026-11-04."), [])

    def test_anchors(self):
        target = {"a.md": "# One\n## Two words\n```\n# not a heading\n```\n## Same\n## Same\n"}
        self.assertEqual(cd.anchor_gaps("b.md", "[x](a.md#two-words)", target), [])
        self.assertEqual(cd.anchor_gaps("b.md", "[x](a.md#same-1)", target), [])
        self.assertTrue(cd.anchor_gaps("b.md", "[x](a.md#nope)", target))
        self.assertTrue(cd.anchor_gaps("b.md", "[x](a.md#not-a-heading)", target))
        self.assertTrue(cd.anchor_gaps("a.md", "[x](#nope)", target))


if __name__ == "__main__":
    unittest.main()
