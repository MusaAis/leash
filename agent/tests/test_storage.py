import base64
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent.guardian import GuardianLog
from leash_agent.intents import IntentsError
from leash_agent.storage import check_storage, view_call
from tests.test_chain import make_config


def contract_says(table):
    """A fake RPC: answers each (method, account) with the JSON in `table`, like a node would."""
    calls = []

    def post(url, body):
        params = body["params"]
        args = json.loads(base64.b64decode(params["args_base64"]))
        calls.append((url, params["account_id"], params["method_name"], args))
        value = table[(params["method_name"], args.get("account_id"))]
        return {"jsonrpc": "2.0", "id": "leash", "result": {"result": list(json.dumps(value).encode()), "logs": []}}
    post.calls = calls
    return post


BOUNDS = {"min": "1250000000000000000000", "max": None}


class Storage(unittest.TestCase):
    def test_reads_bounds_and_balances_and_asks_wrap_near_only(self):
        post = contract_says({("storage_balance_bounds", None): BOUNDS, ("storage_balance_of", "a.near"): None,
                              ("storage_balance_of", "b.near"): {"total": "1", "available": "0"}})
        out = check_storage(["a.near", "b.near"], post=post)
        self.assertEqual(out["storage_balance_bounds"], BOUNDS)
        self.assertIsNone(out["storage_balance_of a.near"])
        self.assertEqual(out["storage_balance_of b.near"]["total"], "1")
        self.assertEqual({call[1] for call in post.calls}, {"wrap.near"})
        self.assertTrue(all(call[2].startswith("storage_balance") for call in post.calls))  # views only

    def test_bad_answers_and_bad_accounts_are_errors(self):
        with self.assertRaises(IntentsError):
            check_storage(["Not An Account"], post=contract_says({}))
        with self.assertRaises(IntentsError):
            view_call("storage_balance_bounds", {}, post=lambda url, body: {"error": {"name": "X"}}, url="https://x")
        with self.assertRaises(IntentsError):
            view_call("storage_balance_bounds", {}, post=lambda url, body: {"result": {"result": [1, 2]}}, url="https://x")

    def test_the_rpc_url_must_be_https(self):
        with mock.patch.dict("os.environ", {"LEASH_RPC_URL": "http://insecure"}):
            with self.assertRaises(IntentsError):
                view_call("storage_balance_bounds", {}, post=contract_says({}))

    def test_cli_uses_the_last_probe_address_and_the_refund_account(self):
        post = contract_says({("storage_balance_bounds", None): BOUNDS, ("storage_balance_of", "d" * 64): None,
                              ("storage_balance_of", "r.near"): None})
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            log = GuardianLog(f"{tmp}/g.jsonl")
            log.append({"event": "probe_quote", "response": {"quote": {"depositAddress": "c" * 64}}})
            log.append({"event": "probe_quote", "response": {"quote": {"depositAddress": "d" * 64}}})
            env = {"LEASH_GUARDIAN_LOG": f"{tmp}/g.jsonl", "LEASH_DRY_REFUND_TO": "r.near"}
            with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out):
                code = cli.wrap_storage([], post=post)
        self.assertEqual(code, 0)
        self.assertIn("no transaction, no key", out.getvalue())
        self.assertIn(f"storage_balance_of {'d' * 64}: null", out.getvalue())
        self.assertNotIn("c" * 64, out.getvalue())


if __name__ == "__main__":
    unittest.main()
