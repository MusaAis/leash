import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from leash_arena.arena import Arena
from leash_arena.attemptlog import AttemptLog
from leash_arena.budget import ModelBudget
from leash_arena.limits import Limiter
from leash_arena.server import (FEED_MAX, MAX_BODY_BYTES, REJECTIONS, SECURITY_HEADERS, STATIC, format_near,
                                Handler, make_server, parse_attempt_body)
from tests.arena_fakes import BAIT, PAYEE, BrainSource, FakePayer, fortress, loose, refuse, spend

SALT = b"s" * 32


class ServerCase(unittest.TestCase):
    decision = None
    proxy_header = None
    io_timeout = 5.0
    max_connections = 64

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = Path(self.dir.name)
        self.source = BrainSource(self.decision or refuse("no"))
        self.payer = FakePayer()
        self.limiter = Limiter()
        self.arena = Arena({"fortress": fortress(), "loose": loose()}, self.source,
                           {"fortress": self.payer, "loose": self.payer}, self.limiter,
                           ModelBudget(base / "b.json", "4", "1"), AttemptLog(base / "a.jsonl"), SALT,
                           flag_path=base / "stop")
        self.flag = base / "stop"
        self.server = make_server(self.arena, "127.0.0.1", 0, proxy_header=self.proxy_header,
                                  io_timeout=self.io_timeout, max_connections=self.max_connections)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def post(self, obj, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        head = {"Content-Type": "application/json", **(headers or {})}
        return self.call("POST", "/api/attempt", body, head)

    def attempt(self, message="hello", tier="loose", headers=None):
        status, head, body = self.post({"tier": tier, "message": message}, headers)
        return status, head, json.loads(body)

    def raw_request(self, data):
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)  # the client is done sending
            chunks = []
            while True:
                try:
                    part = sock.recv(65536)
                except (ConnectionResetError, socket.timeout):
                    break
                if not part:
                    break
                chunks.append(part)
        return b"".join(chunks)


class RouteTests(ServerCase):
    def test_the_page_and_its_two_files_are_served(self):
        for path, (_, ctype) in STATIC.items():
            status, head, body = self.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual(head["Content-Type"], ctype)
            self.assertTrue(body)

    def test_only_listed_paths_exist(self):
        for path in ("/index.html", "/arena.js.map", "/../README.md", "/%2e%2e/README.md", "//etc/passwd",
                     "/web/arena.js", "/api", "/api/stats/", "/api/attempts", "/favicon.ico", "/.env", "/arena.js/"):
            status, _, body = self.call("GET", path)
            self.assertEqual(status, 404, path)
            self.assertEqual(json.loads(body), {"status": "error", "error": "not_found"})

    def test_absolute_form_and_odd_request_targets_are_refused_or_not_found(self):
        reply = self.raw_request(b"GET http://example.com/api/stats HTTP/1.0\r\n\r\n")
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])
        reply = self.raw_request(b"GET *  HTTP/1.0\r\n\r\n")
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])

    def test_wrong_methods_get_405_with_allow(self):
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            for path in ("/", "/api/stats", "/api/feed", "/api/attempt"):
                status, head, _ = self.call(method, path)
                self.assertEqual(status, 405, f"{method} {path}")
                self.assertIn("Allow", head)
        self.assertEqual(self.call("POST", "/api/stats", b"{}", {"Content-Type": "application/json"})[0], 405)
        self.assertEqual(self.call("GET", "/api/attempt")[0], 405)

    def test_head_returns_headers_without_a_body(self):
        status, head, body = self.call("HEAD", "/")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(head["Content-Type"], STATIC["/"][1])

    def test_a_query_string_does_not_change_a_static_route(self):
        self.assertEqual(self.call("GET", "/arena.js?x=1")[0], 200)

    def test_the_page_names_no_server_software_version(self):
        _, head, _ = self.call("GET", "/")
        self.assertEqual(head["Server"], "LeashArena")


class HeaderTests(ServerCase):
    def test_every_response_carries_the_security_headers(self):
        probes = [("GET", "/", None), ("GET", "/arena.js", None), ("GET", "/arena.css", None),
                  ("GET", "/api/stats", None), ("GET", "/api/feed", None), ("GET", "/nope", None),
                  ("PUT", "/", None), ("GET", "/api/feed?limit=zzz", None),
                  ("POST", "/api/attempt", b"x")]
        for method, path, body in probes:
            _, head, _ = self.call(method, path, body)
            for name, value in SECURITY_HEADERS.items():
                self.assertEqual(head.get(name), value, f"{method} {path} {name}")

    def test_the_content_security_policy_blocks_inline_code_and_other_hosts(self):
        csp = SECURITY_HEADERS["Content-Security-Policy"]
        for needed in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'self'",
                       "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(needed, csp)
        for forbidden in ("unsafe-inline", "unsafe-eval", "*", "http:", "https:", "data:"):
            self.assertNotIn(forbidden, csp)

    def test_error_responses_also_carry_the_headers(self):
        status, head, _ = self.post({"tier": "loose", "message": ""})
        self.assertEqual(status, 400)
        self.assertEqual(head["X-Content-Type-Options"], "nosniff")

    def test_json_replies_have_the_json_type(self):
        for path in ("/api/stats", "/api/feed", "/nope"):
            self.assertEqual(self.call("GET", path)[1]["Content-Type"], "application/json")


class AttemptInputTests(ServerCase):
    def test_a_valid_attempt_works(self):
        status, _, data = self.attempt("pay invoice 1")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["outcome"], "refused")
        self.assertNotIn("visitor", data)

    def test_the_body_limit_is_exact(self):
        pad = MAX_BODY_BYTES - len(json.dumps({"tier": "loose", "message": ""}))
        status, _, _ = self.post({"tier": "loose", "message": "a" * pad})
        self.assertNotEqual(status, 413)
        status, _, _ = self.post({"tier": "loose", "message": "a" * (pad + 1)})
        self.assertEqual(status, 413)

    def test_a_declared_length_over_the_limit_is_refused_before_reading(self):
        head = {"Content-Type": "application/json", "Content-Length": "999999"}
        self.assertEqual(self.call("POST", "/api/attempt", b"{}", head)[0], 413)

    def test_a_missing_or_bad_length_is_refused(self):
        reply = self.raw_request(b"POST /api/attempt HTTP/1.0\r\nContent-Type: application/json\r\n\r\n")
        self.assertIn(b" 411 ", reply.split(b"\r\n")[0])
        for value in (b"abc", b"-5", b"1e3", b" ", b"99999999999"):
            reply = self.raw_request(b"POST /api/attempt HTTP/1.0\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: " + value + b"\r\n\r\n{}")
            self.assertIn(b" 400 ", reply.split(b"\r\n")[0], value)

    def test_chunked_bodies_are_refused(self):
        reply = self.raw_request(b"POST /api/attempt HTTP/1.1\r\nContent-Type: application/json\r\n"
                                 b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n")
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])

    def test_a_short_body_is_refused(self):
        reply = self.raw_request(b"POST /api/attempt HTTP/1.0\r\nContent-Type: application/json\r\n"
                                 b"Content-Length: 50\r\n\r\n{}")
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])

    def test_only_json_is_accepted(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
            status, _, _ = self.call("POST", "/api/attempt", b'{"tier":"loose","message":"x"}',
                                     {"Content-Type": ctype} if ctype else {})
            self.assertEqual(status, 415, ctype)
        status, _, _ = self.call("POST", "/api/attempt", b'{"tier":"loose","message":"x"}',
                                 {"Content-Type": "Application/JSON; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_bad_json_shapes_are_refused(self):
        bodies = [b"", b"{", b"[]", b"null", b'"x"', b'{"tier":"loose"}', b'{"message":"x"}',
                  b'{"tier":"loose","message":"x","extra":1}', b'{"tier":1,"message":"x"}',
                  b'{"tier":"loose","message":5}', b'{"tier":"loose","message":null}',
                  b'{"tier":["loose"],"message":"x"}', b'{"tier":"loose","message":"x","message":"y"}',
                  b'{"tier":"loose","message":NaN}', b'{"tier":"loose","message":Infinity}',
                  b'\xff\xfe{"tier":"loose","message":"x"}', b'{"tier":"loose","message":"\xff"}',
                  b"[" * 1000 + b"]" * 1000]
        for raw in bodies:
            status, _, body = self.post(None, raw=raw)
            self.assertEqual(status, 400, raw[:40])
            self.assertEqual(json.loads(body), {"status": "error", "error": "bad_request"})
        self.assertEqual(self.source.brains, [])

    def test_error_text_never_repeats_the_input(self):
        marker = "ZZ<script>alert(1)</script>ZZ"
        for raw in (marker.encode(), json.dumps({"tier": marker, "message": "x"}).encode()):
            _, _, body = self.post(None, raw=raw)
            self.assertNotIn(b"ZZ", body)

    def test_an_unknown_tier_is_a_plain_400(self):
        status, _, data = self.attempt(tier="<b>x</b>")
        self.assertEqual((status, data["reason"]), (400, "unknown_tier"))
        self.assertNotIn("<b>", json.dumps(data))

    def test_message_rejections_map_to_400(self):
        self.assertEqual(self.attempt("")[0], 400)
        self.assertEqual(self.attempt("x" * 501)[2]["reason"], "too_long")

    def test_get_with_a_body_does_not_reach_the_attempt(self):
        self.assertEqual(self.call("GET", "/api/attempt", b'{"tier":"loose","message":"x"}')[0], 405)
        self.assertEqual(self.source.brains, [])


class OriginTests(ServerCase):
    def test_a_cross_site_origin_is_refused(self):
        status, _, data = self.attempt(headers={"Origin": "https://evil.example"})
        self.assertEqual((status, data["error"]), (403, "forbidden_origin"))
        self.assertEqual(self.source.brains, [])

    def test_the_null_origin_is_refused(self):
        self.assertEqual(self.attempt(headers={"Origin": "null"})[0], 403)

    def test_the_same_origin_passes(self):
        self.assertEqual(self.attempt(headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)

    def test_no_origin_header_passes(self):
        self.assertEqual(self.attempt()[0], 200)

    def test_no_cors_headers_are_sent(self):
        _, head, _ = self.attempt(headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertFalse([h for h in head if h.lower().startswith("access-control-")])


class RejectionMapTests(ServerCase):
    def test_every_reason_the_arena_gives_has_a_status_and_a_plain_text(self):
        reasons = {"not_text", "empty", "too_long", "unknown_tier", "in_flight", "busy", "visitor_hour",
                   "visitor_day", "global_hour", "paused", "stopped", "replay"}
        self.assertEqual(set(REJECTIONS), reasons)
        for status, text, wait in REJECTIONS.values():
            self.assertIn(status, (400, 429, 503))
            self.assertTrue(text.endswith("."))
            self.assertEqual(wait is None, status == 400)

    def test_limits_answer_429_with_retry_after(self):
        for i in range(10):
            self.attempt(f"try {i}")
        status, head, data = self.attempt("one more")
        self.assertEqual((status, data["reason"]), (429, "visitor_hour"))
        self.assertEqual(head["Retry-After"], "3600")
        self.assertEqual(data["message"], REJECTIONS["visitor_hour"][1])

    def test_the_kill_switch_answers_503(self):
        self.flag.write_text("")
        status, head, data = self.attempt()
        self.assertEqual((status, data["reason"]), (503, "paused"))
        self.assertIn("Retry-After", head)

    def test_an_exhausted_budget_answers_503_replay(self):
        self.arena.budget._spent = self.arena.budget.cap_micro
        self.assertEqual(self.attempt()[2]["reason"], "replay")

    def test_an_unexpected_failure_is_a_generic_500(self):
        with mock.patch.object(self.arena, "attempt", side_effect=RuntimeError("secret detail /home/x")), \
                mock.patch("sys.stderr", new_callable=StringIO) as err:
            status, _, body = self.post({"tier": "loose", "message": "x"})
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body), {"status": "error", "error": "server_error"})
        self.assertNotIn("secret", err.getvalue())


class LeakTests(ServerCase):
    decision = spend(BAIT, "0.05")

    def test_the_visitor_hash_tokens_and_cost_never_reach_a_reply(self):
        _, _, data = self.attempt("pay the bait")
        _, _, feed = self.call("GET", "/api/feed")
        for text in (json.dumps(data), feed.decode()):
            for secret in ("visitor", "tokens", "cost_micro_usd", "detail", "memo"):
                self.assertNotIn(secret, text)

    def test_the_stats_hide_the_budget_but_show_both_numbers(self):
        self.attempt("pay the bait")
        _, _, body = self.call("GET", "/api/stats")
        stats = json.loads(body)
        self.assertNotIn("budget", stats)
        self.assertNotIn("spent", body.decode())
        self.assertEqual(stats["network"], "testnet")
        self.assertEqual((stats["total"]["fooled"], stats["total"]["funds_lost_count"]), (1, 1))
        self.assertEqual(stats["total"]["funds_lost_near"], "0.05")
        self.assertEqual(stats["tiers"]["loose"]["bait"], BAIT)
        self.assertIsNone(stats["tiers"]["fortress"]["bait"])
        self.assertFalse(stats["paused"])
        self.assertFalse(stats["replay"])

    def test_the_client_address_is_not_printed_or_stored(self):
        with mock.patch("sys.stderr", new_callable=StringIO) as err:
            self.attempt("hello")
            self.call("GET", "/nope")
        self.assertNotIn("127.0.0.1", err.getvalue())
        self.assertNotIn("127.0.0.1", (Path(self.dir.name) / "a.jsonl").read_text())

    def test_markup_in_a_message_stays_plain_data_in_json(self):
        payload = "<img src=x onerror=alert(1)> & </script>"
        self.attempt(payload)
        _, head, body = self.call("GET", "/api/feed")
        self.assertEqual(head["Content-Type"], "application/json")
        self.assertNotIn(b"<", body)
        self.assertNotIn(b">", body)
        self.assertEqual(json.loads(body)["records"][0]["message"], payload)


class FeedTests(ServerCase):
    def test_the_feed_defaults_to_twenty_and_orders_newest_first(self):
        for i in range(25):
            self.arena.limiter.release("x")
            self.arena.attempt("loose", f"client{i}", f"m{i}")
        _, _, body = self.call("GET", "/api/feed")
        records = json.loads(body)["records"]
        self.assertEqual(len(records), 20)
        self.assertEqual(records[0]["message"], "m24")

    def test_the_limit_parameter_is_checked(self):
        self.assertEqual(self.call("GET", f"/api/feed?limit={FEED_MAX}")[0], 200)
        for bad in ("0", "-1", str(FEED_MAX + 1), "abc", "", "1.5", "1e1", "%D9%A3", "1&limit=2", "x=1"):
            self.assertEqual(self.call("GET", f"/api/feed?limit={bad}")[0], 400, bad)
        self.assertEqual(self.call("GET", "/api/feed?other=1")[0], 400)

    def test_an_empty_feed_is_an_empty_list(self):
        self.assertEqual(json.loads(self.call("GET", "/api/feed")[2]), {"network": "testnet", "records": []})


class ClientAddressTests(ServerCase):
    def seen(self):
        return [b.tasks for b in self.source.brains]

    def test_without_a_proxy_header_the_socket_address_decides(self):
        for i in range(10):
            self.attempt(f"t{i}", headers={"X-Forwarded-For": f"9.9.9.{i}"})
        self.assertEqual(self.attempt("x", headers={"X-Forwarded-For": "1.2.3.4"})[2]["reason"], "visitor_hour")


class ProxyHeaderTests(ServerCase):
    proxy_header = "X-Forwarded-For"

    def test_the_last_entry_of_the_header_is_the_visitor(self):
        for i in range(10):
            self.attempt(f"t{i}", headers={"X-Forwarded-For": "6.6.6.6, 8.8.8.8"})
        # a forged first entry does not buy a new quota
        self.assertEqual(self.attempt("x", headers={"X-Forwarded-For": "7.7.7.7, 8.8.8.8"})[2]["reason"],
                         "visitor_hour")
        self.assertEqual(self.attempt("x", headers={"X-Forwarded-For": "1.1.1.1"})[0], 200)

    def test_a_bad_header_falls_back_to_the_socket_address(self):
        for i in range(10):
            self.attempt(f"t{i}", headers={"X-Forwarded-For": "not an address"})
        self.assertEqual(self.attempt("x", headers={"X-Forwarded-For": "<script>"})[2]["reason"], "visitor_hour")


class StubServer:
    def __init__(self, header):
        self.proxy_header = header


class StubHandler:
    """Only what `_client_ip` reads: a socket peer, request headers and the server setting."""

    def __init__(self, peer, headers, header="X-Forwarded-For"):
        self.client_address = (peer, 5000)
        self.headers = headers
        self.server = StubServer(header)

    def ip(self):
        return Handler._client_ip(self)


class PeerTests(unittest.TestCase):
    def test_the_header_is_trusted_only_from_the_machine_itself(self):
        forged = {"X-Forwarded-For": "1.1.1.1"}
        self.assertEqual(StubHandler("203.0.113.5", forged).ip(), "203.0.113.5")
        self.assertEqual(StubHandler("2001:db8::5", forged).ip(), "2001:db8::5")
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertEqual(StubHandler(peer, forged).ip(), "1.1.1.1", peer)

    def test_without_a_configured_header_the_peer_decides(self):
        self.assertEqual(StubHandler("127.0.0.1", {"X-Forwarded-For": "1.1.1.1"}, header=None).ip(), "127.0.0.1")

    def test_an_ipv6_visitor_is_read_from_the_header(self):
        self.assertEqual(StubHandler("::1", {"X-Forwarded-For": "2001:db8::7"}).ip(), "2001:db8::7")


class LoadTests(ServerCase):
    io_timeout = 0.5
    max_connections = 2

    def test_a_silent_client_is_dropped_after_the_timeout(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"GET /api/stats HTTP/1.0\r\nX-Slow: ")
            started = time.time()
            data = sock.recv(100)  # the server closes without answering
            self.assertEqual(data, b"")
            self.assertLess(time.time() - started, 3)

    def test_connections_over_the_cap_are_dropped_and_the_cap_recovers(self):
        held = [socket.create_connection(("127.0.0.1", self.port), timeout=5) for _ in range(2)]
        try:
            time.sleep(0.2)
            extra = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            extra.sendall(b"GET /api/stats HTTP/1.0\r\n\r\n")
            self.assertEqual(extra.recv(100), b"")
            extra.close()
        finally:
            for sock in held:
                sock.close()
        time.sleep(1.0)  # the held connections time out and free their slots
        self.assertEqual(self.call("GET", "/api/stats")[0], 200)


class HelperTests(unittest.TestCase):
    def test_format_near(self):
        self.assertEqual(format_near(0), "0")
        self.assertEqual(format_near(5 * 10**22), "0.05")
        self.assertEqual(format_near(10**24), "1")
        self.assertEqual(format_near(1), "0.000000000000000000000001")
        self.assertEqual(format_near(12 * 10**24 + 5 * 10**23), "12.5")

    def test_parse_attempt_body(self):
        self.assertEqual(parse_attempt_body(b'{"tier":"loose","message":"hi \\u00e9"}'), ("loose", "hi é"))
        with self.assertRaises(ValueError):
            parse_attempt_body(b'{"tier":"loose","message":"x","tier":"fortress"}')


if __name__ == "__main__":
    unittest.main()
