"""Each docs check fails on a bad fixture and passes on a good one."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_docs", Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py")
cd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cd)

SOURCE = "impl V {\n    pub fn new(a: u8) {}\n    pub fn spend(&mut self) {}\n}\n#[cfg(test)]\nmod t { pub fn helper() {} }\n"
SHA = "a" * 64


class DocsChecks(unittest.TestCase):
    def test_stale_phrase(self):
        self.assertTrue(cd.stale_phrases("d.md", "The swap is in progress."))
        self.assertTrue(cd.stale_phrases("d.md", "arrival (the owner's statement)"))
        self.assertEqual(cd.stale_phrases("d.md", "Every pending approval is revoked."), [])

    def test_private_shape(self):
        for bad in ("LE" + "AD decided", "see Re" + "view 018", "finding F" + "-001", "Pat" + "ch 6 adds", "R17" + "-8"):
            self.assertTrue(cd.private_shapes("d.md", bad), bad)
        self.assertEqual(cd.private_shapes("d.md", "Near Builders and a leader"), [])

    def test_interface(self):
        self.assertEqual(cd.interface_gaps(SOURCE, "`new(a)` `spend()`"), [])
        self.assertEqual(len(cd.interface_gaps(SOURCE, "`new(a)`")), 1)

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

    def test_anchor(self):
        target = {"a.md": "# One\n## Two words\n"}
        self.assertEqual(cd.anchor_gaps("b.md", "[x](a.md#two-words)", target), [])
        self.assertTrue(cd.anchor_gaps("b.md", "[x](a.md#nope)", target))
        self.assertTrue(cd.anchor_gaps("a.md", "[x](#nope)", target))


if __name__ == "__main__":
    unittest.main()
