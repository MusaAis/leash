"""Checks the decision collector against fixture answers and the replay script."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import collect_decisions as cd  # noqa: E402
import replay_head  # noqa: E402
import tx_gas  # noqa: E402

VAULT = "v.testnet"
TX_A, TX_B = "A" * 44, "B" * 44


def event(name, n=0):
    return "EVENT_JSON:" + json.dumps({"standard": "leash", "version": "0.3.0", "event": name, "data": [{"n": n}]}, separators=(",", ":"))


def receipt(executor, logs, status=None):
    return {"outcome": {"executor_id": executor, "logs": logs, "status": status or {"SuccessValue": ""}}}


class Collect(unittest.TestCase):
    def test_only_decision_events_of_succeeded_vault_receipts_are_kept(self):
        result = {"receipts_outcome": [
            receipt(VAULT, [event("spend_allowed", 1), event("paused_all"), "plain log"]),
            receipt("other.testnet", [event("spend_denied", 2)]),
            receipt(VAULT, [event("intent_denied", 3)], {"Failure": {"ActionError": {}}}),
            receipt(VAULT, [event("intent_sent", 4), event("spend_denied", 5)]),
            receipt(VAULT, ["EVENT_JSON:not json", event("unknown_event", 6)]),
        ]}
        self.assertEqual(cd.decision_lines(result, VAULT), [event("spend_allowed", 1), event("intent_sent", 4), event("spend_denied", 5)])

    def test_the_collected_lines_replay_to_the_head_and_a_change_breaks_it(self):
        answers = {TX_A: {"receipts_outcome": [receipt(VAULT, [event("spend_allowed", 1)])]},
                   TX_B: {"receipts_outcome": [receipt(VAULT, [event("spend_denied", 2)])]}}
        fetch = lambda method, params: answers[params["tx_hash"]]
        lines = cd.collect(fetch, VAULT, "a.testnet", [TX_A, TX_B])
        head = replay_head.head_of(lines)
        self.assertTrue(replay_head.verify(lines, head))
        self.assertFalse(replay_head.verify(lines[::-1], head))
        self.assertFalse(replay_head.verify(lines[:1], head))
        self.assertFalse(replay_head.verify([lines[0].replace("spend", "spent"), lines[1]], head))

    def test_the_request_names_the_signer_and_waits_for_final(self):
        seen = []
        cd.collect(lambda m, p: seen.append((m, p)) or {"receipts_outcome": []}, VAULT, "a.testnet", [TX_A])
        self.assertEqual(seen, [("EXPERIMENTAL_tx_status", {"tx_hash": TX_A, "sender_account_id": "a.testnet", "wait_until": "FINAL"})])

    def test_hashes_are_found_in_text_once_and_in_order(self):
        text = f"row 1 tx {TX_B} ok\nrow 2 tx {TX_A}\nagain {TX_B}\nshort abc and a 20-char word aaaaaaaaaaaaaaaaaaaa"
        self.assertEqual(cd.tx_hashes(text), [TX_B, TX_A])

    def test_the_head_view_answer_is_decoded(self):
        raw = list(json.dumps("ab" * 32).encode())
        self.assertEqual(cd.read_head(lambda m, p: {"result": raw}, VAULT), "ab" * 32)

    def test_a_public_key_in_the_text_is_not_a_hash(self):
        text = f"Public key: ed25519:{'C' * 44}\nTransaction ID: {TX_A}\nhttps://x.org/transactions/{TX_A}"
        self.assertEqual(cd.tx_hashes(text), [TX_A])

    def test_gas_is_listed_for_the_transaction_and_each_receipt(self):
        result = {"transaction_outcome": {"outcome": {"executor_id": "a.testnet", "gas_burnt": 315000000000}},
                  "receipts_outcome": [{"outcome": {"executor_id": VAULT, "gas_burnt": 3094000000000}}]}
        self.assertEqual(tx_gas.gas_rows(result), [("a.testnet", 0.315), (VAULT, 3.094)])


if __name__ == "__main__":
    unittest.main()
