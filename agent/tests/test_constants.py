"""The Python mirrors of the contract constants cannot drift: they are read from contract/src/lib.rs and compared."""
import re
import unittest
from pathlib import Path

from leash_agent import callpath, config, guardian

ROOT = Path(__file__).resolve().parents[2]


def contract_constants() -> dict[str, int]:
    """Every `const NAME: uN = expr;` of the contract, evaluated (numbers, `*`, `_`, other constants, `.pow(n)`)."""
    source = (ROOT / "contract" / "src" / "lib.rs").read_text()
    found: dict[str, int] = {}
    for name, expr in re.findall(r"^const ([A-Z_]+): u(?:\d+|size) = ([^;]+);", source, re.M):
        text = re.sub(r"(\d)_(?=\d)", r"\1", expr)
        text = re.sub(r"(\d+)u(?:\d+|size)\.pow\((\d+)\)", r"(\1**\2)", text)
        text = re.sub(r"(\d)u(?:\d+|size)", r"\1", text)
        for known, value in found.items():
            text = re.sub(rf"\b{known}\b", str(value), text)
        if not re.fullmatch(r"[0-9 *+()]+", text.replace("**", "*")):
            raise AssertionError(f"cannot evaluate {name} = {expr!r}")
        found[name] = eval(text, {"__builtins__": {}})  # only digits, spaces and * + ( ) remain
    return found


class Constants(unittest.TestCase):
    def setUp(self):
        self.c = contract_constants()

    def test_the_reader_finds_the_contract_values(self):
        self.assertEqual(self.c["RESERVE_BUFFER"], 10**23)
        self.assertEqual(self.c["MAX_APPROVAL_NS"], 30 * 60 * 10**9)
        self.assertEqual(self.c["INTENT_OVERHEAD"], 2 * self.c["STORAGE_DEPOSIT"])

    def test_gas_constants_match(self):
        self.assertEqual(self.c["MIN_INTENT_GAS_TGAS"], config.INTENT_GAS_MIN_TGAS)
        self.assertEqual(self.c["ACTION_GAS_TGAS"], config.ACTION_GAS_TGAS)
        self.assertEqual(self.c["MIN_UNWRAP_GAS_TGAS"], config.MIN_UNWRAP_GAS_TGAS)
        self.assertEqual(self.c["UNWRAP_ACTION_GAS_TGAS"], config.UNWRAP_ACTION_GAS_TGAS)
        self.assertEqual((self.c["MIN_INTENT_GAS_TGAS"], self.c["ACTION_GAS_TGAS"], self.c["MIN_UNWRAP_GAS_TGAS"], self.c["UNWRAP_ACTION_GAS_TGAS"]),
                         (120, 10, 30, 20))

    def test_the_gas_numbers_make_sense_together(self):
        self.assertGreaterEqual(config.INTENT_GAS_MIN_TGAS, 4 * config.ACTION_GAS_TGAS)  # four batch actions fit
        self.assertGreaterEqual(config.MIN_UNWRAP_GAS_TGAS, config.UNWRAP_ACTION_GAS_TGAS)
        self.assertEqual(config.INTENT_GAS_DEFAULT_TGAS, 150)
        self.assertLessEqual(config.INTENT_GAS_MAX_TGAS, 300)  # the transaction limit

    def test_the_agent_default_is_inside_the_allowed_range(self):
        self.assertTrue(config.INTENT_GAS_MIN_TGAS <= config.INTENT_GAS_DEFAULT_TGAS <= config.INTENT_GAS_MAX_TGAS)

    def test_storage_deposit_and_overhead_match(self):
        self.assertEqual(self.c["STORAGE_DEPOSIT"], guardian.STORAGE_DEPOSIT)
        self.assertEqual(self.c["INTENT_OVERHEAD"], guardian.INTENT_OVERHEAD)
        self.assertEqual(self.c["RESERVE_BUFFER"], 10**23)

    def test_the_approval_lifetime_stays_below_the_contract_limit(self):
        self.assertLess(callpath.MAX_APPROVAL_MINUTES * 60 * 10**9, self.c["MAX_APPROVAL_NS"])
        self.assertEqual(callpath.MAX_APPROVAL_MINUTES + 1, self.c["MAX_APPROVAL_NS"] // (60 * 10**9))

    def test_route_ids_match_the_contract_limit(self):
        self.assertEqual(guardian.MAX_ROUTE_ID + 1, self.c["MAX_ROUTES"])


if __name__ == "__main__":
    unittest.main()
