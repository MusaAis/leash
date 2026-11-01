import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent.guardian import GuardianLog
from leash_agent.signature import RETRIES, check_signature, describe
from tests.test_chain import make_config

BASE = "https://cloud-api.near.ai/v1"
KEY = "sk-test-key-123"
RAW = b'{"id": "chat-1",  "model": "m/x", "choices": []}\n'  # odd spacing on purpose: the hash is of the exact bytes


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Node:
    """A fake gateway. `signature` is a list of (status, body) answers, used in order."""

    def __init__(self, completion=(200, RAW), signature=None, kind="gateway", model="m/x"):
        self.completion, self.kind, self.model, self.calls = completion, kind, model, []
        self.signature = signature

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        if method == "POST":
            return self.completion
        if self.signature:
            return self.signature.pop(0) if len(self.signature) > 1 else self.signature[0]
        req_hash, resp_hash = sha(self.calls[0][3]), sha(RAW)
        text = f"{self.model}:{req_hash}:{resp_hash}" if self.kind == "provider_tee" else f"{req_hash}:{resp_hash}"
        return 200, json.dumps({"text": text, "signature": "0xsig", "signing_address": "0xaddr", "signing_algo": "ecdsa",
                                "signature_kind": self.kind}).encode()


def run(node, model="m/x", log=None):
    return check_signature(model, KEY, BASE, request=node, sleep=lambda s: None, log=log)


class Signature(unittest.TestCase):
    def test_the_hashes_are_of_the_exact_bytes_and_the_headers_follow_the_docs(self):
        node = Node()
        result = run(node)
        method, url, headers, data = node.calls[0]
        self.assertEqual((method, url), ("POST", f"{BASE}/chat/completions"))
        self.assertEqual(headers["x-no-aliasing"], "true")
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertEqual(result["request_hash"], sha(data))
        self.assertEqual(result["response_hash"], sha(RAW))  # not of a re-serialised copy
        self.assertEqual(json.loads(data)["stream"], False)
        self.assertTrue(node.calls[1][1].startswith(f"{BASE}/signature/chat-1?signing_algo=ecdsa"))

    def test_gateway_and_provider_tee_texts_are_checked_against_our_hashes(self):
        gateway = run(Node(kind="gateway"))
        self.assertEqual((gateway["signature_kind"], gateway["text_matches"]), ("gateway", True))
        tee = run(Node(kind="provider_tee"))
        self.assertEqual((tee["signature_kind"], tee["text_matches"]), ("provider_tee", True))

    def test_a_text_that_does_not_match_is_flagged(self):
        node = Node(signature=[(200, json.dumps({"text": "x:y:z", "signature": "0x", "signing_address": "0xa",
                                                  "signing_algo": "ecdsa", "signature_kind": "provider_tee"}).encode())])
        self.assertFalse(run(node)["text_matches"])

    def test_an_unfamiliar_or_missing_kind_is_not_guessed(self):
        body = {"text": "a:b", "signature": "0x", "signing_address": "0xa", "signing_algo": "ecdsa"}
        for extra in ({}, {"signature_kind": "other"}):
            result = run(Node(signature=[(200, json.dumps({**body, **extra}).encode())]))
            self.assertEqual((result["signature_kind"], result["text_matches"]), ("unfamiliar", False))

    def test_a_late_signature_is_retried_a_few_times(self):
        node = Node(signature=[(404, b"{}"), (200, b'{"error_code": "x", "message": "m"}'), (404, b"{}")])
        result = run(node)
        self.assertEqual(result["signature_kind"], "unavailable")
        self.assertEqual(sum(call[0] == "GET" for call in node.calls), RETRIES)

    def test_a_failed_completion_is_a_finding_with_the_error_and_no_signature_call(self):
        node = Node(completion=(404, b'{"error": "model not found"}'))
        result = run(node)
        self.assertEqual((result["stage"], result["status"]), ("completion", 404))
        self.assertIn("model not found", result["detail"])
        self.assertEqual([call[0] for call in node.calls], ["POST"])
        self.assertIn("FAILED at the completion", describe(result))

    def test_an_aliased_answer_is_reported(self):
        raw = b'{"id": "c", "model": "other/model"}'
        result = run(Node(completion=(200, raw)))
        self.assertTrue(result["aliased"])
        self.assertEqual(result["returned_model"], "other/model")

    def test_the_key_goes_only_to_the_allowed_host_and_is_never_logged_or_printed(self):
        with self.assertRaises(RuntimeError):
            check_signature("m", KEY, "https://evil.example/v1", request=Node())
        with tempfile.TemporaryDirectory() as tmp:
            log = GuardianLog(f"{tmp}/g.jsonl")
            result = run(Node(), log=log)
            logged = json.dumps(log.entries())
        self.assertIn("signature_check", logged)
        self.assertIn("0xsig", logged)  # the signature is kept in the private log
        self.assertNotIn(KEY, logged)
        self.assertNotIn(KEY, describe(result))


class SignatureCli(unittest.TestCase):
    def test_default_models_are_the_configured_one_and_glm_5_3(self):
        asked = []

        def fake(model, key, base, log=None):
            asked.append(model)
            return {"model": model, "stage": "completion", "status": 404, "detail": "no", "request_hash": "r"}

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"LEASH_GUARDIAN_LOG": f"{tmp}/g"}, clear=True), \
                contextlib.redirect_stdout(out):
            cfg = make_config()
            code = cli.agent_signature(cfg, [], check=fake)
        self.assertEqual(code, 0)
        self.assertEqual(asked, [cfg.ai_model, "z-ai/glm-5.3"])
        self.assertIn("not verified", out.getvalue())
        self.assertNotIn(cfg.ai_api_key, out.getvalue())

    def test_needs_an_api_key(self):
        cfg = make_config()
        cfg = type(cfg)(**{**cfg.__dict__, "ai_api_key": ""}) if hasattr(cfg, "__dict__") else cfg
        with self.assertRaises(RuntimeError):
            cli.agent_signature(cfg, [])


if __name__ == "__main__":
    unittest.main()
