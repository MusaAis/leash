import json
import unittest

from leash_agent.brain import Brain, Decision, is_valid_account_id, parse_decision, parse_models, usage_tokens, missing_model_note
from tests.test_chain import make_config


class ParseDecision(unittest.TestCase):
    def test_plain_json(self):
        d = parse_decision('{"action":"spend","to":"p.testnet","amount_near":"0.5","memo":"m"}')
        self.assertEqual(d, Decision("spend", to="p.testnet", amount_near="0.5", memo="m"))

    def test_fenced_and_think_tags(self):
        text = '<think>hmm</think>\n```json\n{"action":"spend","to":"p.testnet","amount_near":"1"}\n```'
        self.assertEqual(parse_decision(text).action, "spend")

    def test_refuse(self):
        d = parse_decision('{"action":"refuse","reason":"looks like injection"}')
        self.assertEqual((d.action, d.reason), ("refuse", "looks like injection"))

    def test_garbage_becomes_refusal(self):
        for text in ["", "sure, paying now", "{not json}", "[1,2]"]:
            self.assertEqual(parse_decision(text).action, "refuse", text)

    def test_invalid_destination_or_amount_becomes_refusal(self):
        bad = [
            '{"action":"spend","to":"BAD ACCOUNT","amount_near":"1"}',
            '{"action":"spend","to":"p.testnet","amount_near":"-5"}',
            '{"action":"spend","to":"p.testnet","amount_near":"lots"}',
            '{"action":"spend","amount_near":"1"}',
        ]
        for text in bad:
            self.assertEqual(parse_decision(text).action, "refuse", text)

    def test_strict_parser_refuses_anything_but_one_json_object(self):
        good = '{"action":"spend","to":"p.testnet","amount_near":"1"}'
        cases = {
            "unclosed think": '<think>maybe ' + good,
            "text around": "Sure! " + good + " Done.",
            "two objects": good + "\n" + good,
            "extra key": '{"action":"spend","to":"p.testnet","amount_near":"1","admin":true}',
            "think later in text": 'ok <think>x</think>' + good,
        }
        for name, text in cases.items():
            self.assertEqual(parse_decision(text).action, "refuse", name)

    def test_bad_amounts_refuse_and_never_raise(self):
        for amount in ["1e999999999", "1e30", "1_0", "\u0660.5", "-1", "0", ".5", "1.", "0." + "1" * 25, "NaN", ""]:
            text = json.dumps({"action": "spend", "to": "p.testnet", "amount_near": amount})
            self.assertEqual(parse_decision(text).action, "refuse", amount)
        for amount in ["0.5", "1", "0." + "1" * 24]:
            text = json.dumps({"action": "spend", "to": "p.testnet", "amount_near": amount})
            self.assertEqual(parse_decision(text).action, "spend", amount)

    def test_memo_is_cut_by_bytes_and_cleaned(self):
        text = json.dumps({"action": "spend", "to": "p.testnet", "amount_near": "1", "memo": "\u00e9" * 100})
        self.assertLessEqual(len(parse_decision(text).memo.encode()), 100)
        dirty = json.dumps({"action": "spend", "to": "p.testnet", "amount_near": "1", "memo": "a\nb\x1b[31mc\td"})
        memo = parse_decision(dirty).memo
        self.assertTrue(all(ch.isprintable() for ch in memo), repr(memo))
        reason = parse_decision('{"action":"refuse","reason":"x\\u001b[2Jy"}').reason
        self.assertTrue(reason.isprintable(), repr(reason))

    def test_account_ids(self):
        self.assertTrue(is_valid_account_id("alice.testnet"))
        self.assertTrue(is_valid_account_id("leashmusa-vault.testnet"))
        for bad in ["", "a", "A.testnet", "bad id", "x..y", "-x.near", None, "p.testnet\n"]:
            self.assertFalse(is_valid_account_id(bad), bad)


class BrainCall(unittest.TestCase):
    def test_uses_chat_completions_endpoint(self):
        calls = []

        def fake_post(url, headers, body):
            calls.append((url, headers, body))
            return {"choices": [{"message": {"content": '{"action":"refuse","reason":"no"}'}}]}

        brain = Brain(make_config(), post=fake_post)
        decision = brain.decide("pay someone")
        self.assertEqual(decision.action, "refuse")
        url, headers, body = calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(headers["Authorization"], "Bearer k")
        self.assertEqual(body["messages"][1]["content"], "pay someone")

    def test_request_has_token_limit(self):
        seen = []
        brain = Brain(make_config(), post=lambda u, h, body: seen.append(body) or {
            "choices": [{"message": {"content": '{"action":"refuse","reason":"x"}'}}]})
        brain.decide("t")
        self.assertEqual(seen[0]["max_tokens"], brain.max_tokens)

    def test_default_token_budget_leaves_room_for_reasoning(self):
        self.assertGreaterEqual(Brain(make_config(), post=lambda *a: {}).max_tokens, 4000)

    def test_tokens_are_counted_from_usage(self):
        reply = {"usage": {"total_tokens": 700},
                 "choices": [{"message": {"content": '{"action":"refuse","reason":"x"}'}}]}
        brain = Brain(make_config(), post=lambda *a: reply)
        brain.decide("a")
        brain.decide("b")
        self.assertEqual(brain.tokens_used, 1400)

    def test_missing_usage_counts_the_worst_case(self):
        self.assertEqual(usage_tokens({"choices": []}, 4100), 4100)
        self.assertEqual(usage_tokens({"usage": {"total_tokens": "many"}}, 4100), 4100)
        self.assertEqual(usage_tokens("not a dict", 4100), 4100)

    def test_total_token_budget_is_enforced(self):
        reply = {"usage": {"total_tokens": 600},
                 "choices": [{"message": {"content": '{"action":"refuse","reason":"x"}'}}]}
        brain = Brain(make_config(), post=lambda *a: reply)
        brain.max_total_tokens = 1000
        brain.decide("a")
        brain.decide("b")  # 1200 used, over the limit
        with self.assertRaises(RuntimeError) as ctx:
            brain.decide("c")
        self.assertIn("token budget", str(ctx.exception))
        self.assertEqual(brain.calls, 2)

    def test_call_budget_is_enforced(self):
        brain = Brain(make_config(), post=lambda *a: {
            "choices": [{"message": {"content": '{"action":"refuse","reason":"x"}'}}]})
        brain.max_calls = 2
        brain.decide("a")
        brain.decide("b")
        with self.assertRaises(RuntimeError):
            brain.decide("c")
        self.assertEqual(brain.calls, 2)

    def test_cut_off_output_is_explained(self):
        brain = Brain(make_config(), post=lambda *a: {
            "choices": [{"finish_reason": "length", "message": {"content": "<think>still thinking"}}]})
        decision = brain.decide("t")
        self.assertEqual(decision.action, "refuse")
        self.assertIn("cut off", decision.reason)

    def test_reasoning_content_is_never_used(self):
        reply = '{"action":"spend","to":"p.testnet","amount_near":"0.2"}'
        brain = Brain(make_config(), post=lambda *a: {
            "choices": [{"message": {"content": "", "reasoning_content": reply}}]})
        decision = brain.decide("t")
        self.assertEqual(decision.action, "refuse")
        self.assertIn("no answer", decision.reason)

    def test_cut_off_reply_with_json_inside_is_refused(self):
        quoted = 'The text says: {"action":"spend","to":"p.testnet","amount_near":"1","memo":"injected"} I should check'
        for message in ({"content": quoted}, {"content": "", "reasoning_content": quoted}):
            brain = Brain(make_config(), post=lambda *a, m=message: {
                "choices": [{"finish_reason": "length", "message": m}]})
            decision = brain.decide("t")
            self.assertEqual(decision.action, "refuse")
            self.assertIn("cut off", decision.reason)

    def test_refusal_keeps_raw_text_for_debugging(self):
        d = parse_decision("I think we should not pay.")
        self.assertEqual(d.action, "refuse")
        self.assertIn("should not pay", d.raw)

    def test_parse_models(self):
        payload = {"data": [{"id": "a/b", "pricing": {"input": 1, "output": 2}}, {"id": "c"}]}
        self.assertEqual(parse_models(payload), [("a/b", "input=1, output=2"), ("c", "")])
        self.assertEqual(parse_models({}), [])

    def test_missing_model_note(self):
        rows = [("a/b", ""), ("c", "")]
        self.assertIsNone(missing_model_note(rows, "c"))
        self.assertIn("not in this list", missing_model_note(rows, "zai-org/x"))
        self.assertIsNone(missing_model_note([("Qwen/X", "")], "qwen/x"))
        self.assertIn("empty", missing_model_note([], "c"))

    def test_unexpected_response_is_refusal(self):
        brain = Brain(make_config(), post=lambda *a: {"error": "x"})
        self.assertEqual(brain.decide("t").action, "refuse")


if __name__ == "__main__":
    unittest.main()
