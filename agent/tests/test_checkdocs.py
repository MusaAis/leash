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
REFACTORED = (
    "fn check_spend(s: &S) -> Result<(), &'static str> {\n    if a {\n        return Err(\"all_paused\");\n    }\n"
    "    if b {\n        return Err(\"zero_amount\");\n    }\n    Ok(())\n}\n\nfn other() -> Result<(), &'static str> {\n    Err(\"not_a_reason\")\n}\n"
)
VIEWS = SOURCE.replace("pub fn spend(&mut self) {}", "pub fn spend(&mut self) {}\n    pub fn would_spend(&self) {}")
SHA = "a" * 64


class DocsChecks(unittest.TestCase):
    def test_stale_phrases_fail_one_by_one(self):
        for bad in ("not released", "work in progress", "not deployed", "a placeholder", "to be measured", "no LEASH contract",
                    "as reported by the API", "swap (reported)", "reported, not shown", "the owner reports", "the owner's report",
                    "(the owner's statement)", "it is inferred", "is unverified", "not yet verified", "the rehearsal records",
                    "1Click reported the swap as SUCCESS", "older versions are untested", "an untested path"):
            self.assertTrue(cd.stale_phrases("d.md", bad), bad)

    def test_stale_phrases_pass(self):
        for good in ("1Click returned status SUCCESS.", "Every pending approval is revoked.", "the owner's policy"):
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
        for bad in ("(guardian 0.080 NEAR, agent 0.250 NEAR on 2026-11-05)", "0.2874 NEAR as of 2026-11-06.", "On 2026-11-06 the vault had 0.29 NEAR.", "The swap ran on 2026-11-04."):
            self.assertTrue(cd.snapshot_problems("s.md", bad), bad)
        self.assertEqual(cd.snapshot_problems("s.md", "See [the table](mainnet-proof.md#2026-11-06) and `2026-11-06`.\n```\n2026-11-06\n```"), [])
        for bad in ("It ran on 6 November 2026.", "It ran on Nov 6.", "It ran on 6th Nov.", "It ran on 2026/11/06.", "It ran on 06/11/2026.", "Read in Nov. 6, 2026.",
                    "As of November 2026.", "Since Nov 2026.", "Run 2026.11.04.", "Run 04.11.2026.", "Run 04-11-2026.", "Run 20261104."):
            self.assertTrue(cd.snapshot_problems("s.md", bad), bad)
        self.assertEqual(cd.snapshot_problems("s.md", "| Run on | 2026-11-06 |\n|---|---|\nNo date here."), [])
        self.assertEqual(cd.snapshot_problems("s.md", "The November release adds 3 methods."), [])
        self.assertEqual(cd.snapshot_problems("s.md", "Gas 20000000 and version 2.6.0 and 1.96.0."), [])
        self.assertTrue(cd.stale_phrases("CHANGELOG.md", "moves creation-key files"))
        self.assertEqual(cd.stale_phrases("CHANGELOG.md", "moves full-access key files"), [])
        from unittest import mock
        with mock.patch.object(cd, "doc_paths", return_value=[]), mock.patch.object(cd, "text", return_value="creation key"):
            self.assertTrue(cd.check_stale())
        with mock.patch.object(cd, "doc_paths", return_value=[]), mock.patch.object(cd, "text", return_value="fine"):
            self.assertEqual(cd.check_stale(), [])

    def test_anchors(self):
        target = {"a.md": "# One\n## Two words\n```\n# not a heading\n```\n## Same\n## Same\n"}
        self.assertEqual(cd.anchor_gaps("b.md", "[x](a.md#two-words)", target), [])
        self.assertEqual(cd.anchor_gaps("b.md", "[x](a.md#same-1)", target), [])
        self.assertTrue(cd.anchor_gaps("b.md", "[x](a.md#nope)", target))
        self.assertTrue(cd.anchor_gaps("b.md", "[x](a.md#not-a-heading)", target))
        self.assertTrue(cd.anchor_gaps("a.md", "[x](#nope)", target))

    def test_denial_reasons_come_from_the_shared_check(self):
        self.assertEqual(cd.denial_reasons(REFACTORED), {"all_paused", "zero_amount"})
        self.assertEqual(cd.denial_problems(REFACTORED, "Denial reasons: `all_paused`, `zero_amount`.\n"), [])
        self.assertEqual(len(cd.denial_problems(REFACTORED, "Denial reasons: `all_paused`.\n")), 1)

    def test_zero_denial_reasons_never_pass(self):
        self.assertEqual(cd.denial_reasons("pub fn spend() {}\n"), set())
        self.assertEqual(len(cd.denial_problems("pub fn spend() {}\n", "Denial reasons: \n")), 1)

    def test_interface_skips_only_the_listed_names(self):
        readme = "`new(a)` `spend(x)` `spend_intent(y)`"
        self.assertEqual(cd.interface_gaps(VIEWS, readme, ["would_spend"]), [])
        self.assertEqual(len(cd.interface_gaps(VIEWS, readme)), 1)

    def test_unreleased_name_must_exist_and_stay_out_of_the_docs(self):
        tags = {"v0.3.1"}
        self.assertEqual(cd.unreleased_problems(VIEWS, {"README.md": "`spend(x)`"}, ["would_spend"], "v0.3.2", tags), [])
        self.assertEqual(len(cd.unreleased_problems(VIEWS, {}, ["would_spend_old"], "v0.3.2", tags)), 1)
        self.assertEqual(len(cd.unreleased_problems(VIEWS, {"README.md": "`would_spend(a)`"}, ["would_spend"], "v0.3.2", tags)), 1)
        self.assertEqual(cd.unreleased_problems(VIEWS, {"README.md": "`would_spend_intent`"}, ["would_spend"], "v0.3.2", tags), [])

    def test_unreleased_list_must_be_empty_once_the_tag_exists(self):
        self.assertEqual(cd.unreleased_problems(VIEWS, {}, ["would_spend"], "v0.3.2", {"v0.3.1"}), [])
        self.assertEqual(cd.unreleased_problems(VIEWS, {}, ["would_spend"], "v0.3.2", set()), [])
        self.assertEqual(len(cd.unreleased_problems(VIEWS, {}, ["would_spend"], "v0.3.2", {"v0.3.1", "v0.3.2"})), 1)
        self.assertEqual(cd.unreleased_problems(VIEWS, {}, [], "v0.3.2", {"v0.3.2"}), [])

    def test_readme_pin_row(self):
        cargo = 'near-sdk = "=5.28.3"\n[dev-dependencies]\nnear-sdk = { version = "=5.28.3", features = ["x"] }\n'
        tool = '[toolchain]\nchannel = "1.96.0"\n'
        badges = "badge/Rust-1.96.0-orange badge/near--sdk-%3D5.28.3-blue "
        row = badges + "| Pinned build inputs | Rust 1.96.0 and near-sdk =5.28.3, see docs |"
        self.assertEqual(cd.readme_pin_problems(row, cargo, tool), [])
        self.assertEqual(len(cd.readme_pin_problems("no row", cargo, tool)), 1)
        self.assertEqual(len(cd.readme_pin_problems(row.replace("1.96.0", "1.95.0"), cargo, tool)), 2)
        self.assertEqual(len(cd.readme_pin_problems(row.replace("5.28.3", "5.30.0"), cargo, tool)), 2)
        self.assertEqual(len(cd.readme_pin_problems(row.replace("badge/Rust-1.96.0-orange", ""), cargo, tool)), 1)
        self.assertEqual(len(cd.readme_pin_problems(row.replace("badge/near--sdk-%3D5.28.3-blue", ""), cargo, tool)), 1)
        self.assertEqual(len(cd.readme_pin_problems(row.replace("Rust-1.96.0-orange", "Rust-1.95.0-orange"), cargo, tool)), 1)


if __name__ == "__main__":
    unittest.main()
