import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from leash_agent.chain import ChainError
from leash_agent.config import Config
from leash_arena.dash import FAILURE_WINDOW, Dashboard, near_text, rpc_view, vault_snapshot
from leash_arena.live import LiveError, Settings
from tests.test_arena_server import ServerCase
from leash_arena.server import make_server

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 40
HASH = hashlib.sha256(TOKEN.encode()).hexdigest()
HEAD = "ab" * 32


class Reply:
    def __init__(self, value):
        self.body = json.dumps({"result": {"result": list(json.dumps(value).encode())}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return self.body


def opener_for(values, seen=None):
    def opener(request, timeout=0):
        params = json.loads(request.data)["params"]
        if seen is not None:
            seen.append(params["method_name"])
        value = values.get(params["method_name"], "missing")
        if value == "missing":
            raise OSError("down")
        return Reply(value)
    return opener


GOOD = {
    "get_policy": {"per_tx_cap": str(5 * 10**22), "daily_cap": str(3 * 10**23), "paused": False,
                   "spent_in_window": str(10**22), "allowlist": ["p.testnet"], "window_start_ns": "0"},
    "get_available_balance": str(2 * 10**24), "get_stats": ["3", "1"], "is_paused_all": False, "get_head_hash": HEAD,
}


def cfg(prefix="arenat"):
    return replace(Config.from_env(), vault=f"{prefix}-vault.testnet", owner=f"{prefix}-owner.testnet",
                   agent=f"{prefix}-agent.testnet")


class ReadTests(unittest.TestCase):
    def test_near_text(self):
        self.assertEqual(near_text(0), "0.0000")
        self.assertEqual(near_text(2 * 10**24 + 5 * 10**21), "2.0050")

    def test_rpc_view_parses_a_view(self):
        self.assertEqual(rpc_view("v.testnet", "get_stats", None, opener_for({"get_stats": ["1", "2"]})), ["1", "2"])

    def test_rpc_view_failure_is_a_chain_error(self):
        with self.assertRaises(ChainError):
            rpc_view("v.testnet", "get_stats", None, opener_for({}))

    def test_snapshot_reads_every_view(self):
        s = vault_snapshot(cfg(), opener_for(GOOD))
        self.assertEqual(s["available_near"], "2.0000")
        self.assertEqual((s["allowed"], s["denied"]), (3, 1))
        self.assertEqual(s["policy"]["per_tx_cap_near"], "0.0500")
        self.assertEqual(s["policy"]["spent_in_window_near"], "0.0100")
        self.assertFalse(s["paused_all"])
        self.assertEqual(s["head_hash"], HEAD)

    def test_snapshot_marks_an_unreadable_view_null(self):
        s = vault_snapshot(cfg(), opener_for({k: v for k, v in GOOD.items() if k != "get_stats"}))
        self.assertIsNone(s["allowed"])
        self.assertEqual(s["available_near"], "2.0000")

    def test_snapshot_ignores_values_in_the_wrong_shape(self):
        s = vault_snapshot(cfg(), opener_for({**GOOD, "get_head_hash": "short", "get_available_balance": 5,
                                              "get_policy": {"per_tx_cap": "x"}}))
        self.assertIsNone(s["head_hash"])
        self.assertIsNone(s["available_near"])
        self.assertIsNone(s["policy"])

    def test_snapshot_is_cached_for_the_ttl(self):
        seen, now = [], [0.0]
        dash = Dashboard({"fortress": cfg()}, Path("/nonexistent/stop"), None, opener_for(GOOD, seen), clock=lambda: now[0])
        dash.snapshot()
        count = len(seen)
        now[0] = 5.0
        dash.snapshot()
        self.assertEqual(len(seen), count)
        now[0] = 20.0
        dash.snapshot()
        self.assertGreater(len(seen), count)


class RefreshTests(unittest.TestCase):
    def test_a_slow_refresh_does_not_hold_other_callers(self):
        import threading
        import time as _time
        now, gate, entered = [0.0], threading.Event(), threading.Event()
        calls = []

        def opener(request, timeout):
            calls.append(timeout)
            entered.set()
            if len(calls) > 6:  # the second round of reads waits for the gate
                gate.wait(5)
            return opener_for(GOOD)(request, timeout)

        dash = Dashboard({"fortress": cfg()}, Path("/nonexistent/stop"), None, opener, clock=lambda: now[0])
        first = dash.snapshot()
        now[0] = 20.0
        entered.clear()
        worker = threading.Thread(target=dash.snapshot)
        worker.start()
        self.assertTrue(entered.wait(5))
        started = _time.time()
        second = dash.snapshot()  # served from the last state while the refresh is still running
        self.assertLess(_time.time() - started, 1.0)
        self.assertEqual(second["tiers"], first["tiers"])
        gate.set()
        worker.join(5)

    def test_the_view_call_has_a_five_second_timeout(self):
        seen = []
        rpc_view("v.testnet", "get_stats", None, lambda request, timeout: (seen.append(timeout), opener_for({"get_stats": [1, 2]})(request, timeout))[1])
        self.assertEqual(seen, [5.0])

    def test_wrong_tokens_are_counted_from_many_threads(self):
        import threading
        dash = Dashboard({}, Path("/nonexistent/stop"), HASH)
        threads = [threading.Thread(target=dash.token_ok, args=("x",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(dash.locked())


class PanicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.flag = Path(self.tmp.name) / "state" / "stop"
        self.paused, self.now = [], [0.0]

    def dash(self, pauser=None, token=HASH):
        def default(c):
            self.paused.append(c.vault)
        return Dashboard({"fortress": cfg("a"), "loose": cfg("b")}, self.flag, token, opener_for(GOOD),
                         pauser or default, clock=lambda: self.now[0])

    def test_right_token_only(self):
        d = self.dash()
        self.assertTrue(d.token_ok(TOKEN))
        self.assertFalse(d.token_ok("wrong"))
        self.assertFalse(d.token_ok(""))

    def test_disabled_without_a_hash(self):
        d = self.dash(token=None)
        self.assertFalse(d.panic_enabled)
        self.assertFalse(d.token_ok(TOKEN))

    def test_five_wrong_tokens_lock_even_the_right_one_until_the_window_passes(self):
        d = self.dash()
        for _ in range(5):
            self.assertFalse(d.token_ok("x"))
        self.assertTrue(d.locked())
        self.assertFalse(d.token_ok(TOKEN))
        self.now[0] = FAILURE_WINDOW + 1
        self.assertTrue(d.token_ok(TOKEN))

    def test_panic_sets_the_stop_file_and_pauses_every_vault(self):
        result = self.dash().panic()
        self.assertTrue(self.flag.exists())
        self.assertEqual(stat.S_IMODE(self.flag.stat().st_mode), 0o600)
        self.assertEqual(sorted(self.paused), ["a-vault.testnet", "b-vault.testnet"])
        self.assertEqual(result, {"stopped": True, "tiers": {"fortress": "paused", "loose": "paused"}})

    def test_a_failed_pause_is_reported_and_the_stop_file_stays(self):
        def pauser(c):
            if c.vault.startswith("b"):
                raise ChainError("secret detail")
        result = self.dash(pauser).panic()
        self.assertEqual(result["tiers"], {"fortress": "paused", "loose": "failed"})
        self.assertNotIn("secret", json.dumps(result))
        self.assertTrue(self.flag.exists())


class SettingsTests(unittest.TestCase):
    def test_hash_must_be_lower_case_hex(self):
        self.assertEqual(Settings.from_env({"LEASH_ARENA_PANIC_SHA256": HASH}).panic_sha256, HASH)
        self.assertEqual(Settings.from_env({}).panic_sha256, "")
        for bad in ("abc", HASH.upper(), HASH + "0"):
            with self.assertRaises(LiveError):
                Settings.from_env({"LEASH_ARENA_PANIC_SHA256": bad})


class DashServerTests(ServerCase):
    token_hash = HASH

    def setUp(self):
        super().setUp()
        self.stop()  # replace the server built by the base class
        self.paused = []
        self.dashboard = Dashboard({"fortress": cfg("a"), "loose": cfg("b")}, self.flag, self.token_hash,
                                   opener_for(GOOD), lambda c: self.paused.append(c.vault))
        self.server = make_server(self.arena, "127.0.0.1", 0, dashboard=self.dashboard)
        self.port = self.server.server_address[1]
        import threading
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def panic(self, token=TOKEN, headers=None):
        head = {"Authorization": f"Bearer {token}", **(headers or {})}
        return self.call("POST", "/api/panic", b"", {**head, "Content-Length": "0"})

    def test_a_slow_pause_is_not_cut_by_the_request_deadline(self):
        import time as _time
        self.stop()
        slow = Dashboard({"fortress": cfg("a")}, self.flag, HASH, opener_for(GOOD), lambda c: _time.sleep(0.8))
        self.server = make_server(self.arena, "127.0.0.1", 0, dashboard=slow, request_deadline=0.3)
        self.port = self.server.server_address[1]
        import threading
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        status, _, body = self.panic()
        self.assertEqual(status, 200)
        self.assertIn(b'"paused"', body)

    def test_the_dashboard_page_and_data_are_served(self):
        for path, kind in (("/dashboard", "text/html"), ("/dash.js", "text/javascript"), ("/dash.css", "text/css")):
            status, head, _ = self.call("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(head["Content-Type"].startswith(kind))
        status, _, body = self.call("GET", "/api/dashboard")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["tiers"]["loose"]["head_hash"], HEAD)
        self.assertTrue(data["panic_enabled"])

    def test_the_page_never_holds_the_token_or_its_hash(self):
        for path in ("/dashboard", "/dash.js", "/api/dashboard"):
            _, _, body = self.call("GET", path)
            self.assertNotIn(TOKEN.encode(), body)
            self.assertNotIn(HASH.encode(), body)

    def test_panic_needs_the_token(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + TOKEN}):
            status, _, _ = self.call("POST", "/api/panic", b"", {**headers, "Content-Length": "0"})
            self.assertEqual(status, 401)
        self.assertEqual(self.paused, [])
        self.assertFalse(self.flag.exists())

    def test_the_right_token_pauses_and_stops(self):
        status, _, body = self.panic()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["tiers"], {"fortress": "paused", "loose": "paused"})
        self.assertTrue(self.flag.exists())
        self.assertEqual(self.attempt()[2].get("reason"), "paused")

    def test_a_cross_origin_call_is_refused_even_with_the_right_token(self):
        status, _, _ = self.panic(headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.paused, [])

    def test_wrong_tokens_lock_the_switch(self):
        for _ in range(5):
            self.assertEqual(self.panic("wrong")[0], 401)
        self.assertEqual(self.panic()[0], 429)
        self.assertEqual(self.paused, [])

    def test_get_on_panic_and_post_on_dashboard_are_refused(self):
        self.assertEqual(self.call("GET", "/api/panic")[0], 405)
        self.assertEqual(self.call("POST", "/api/dashboard", b"", {"Content-Length": "0"})[0], 405)


class DashOffServerTests(ServerCase):
    def test_without_a_dashboard_nothing_of_it_exists(self):
        for method, path in (("GET", "/dashboard"), ("GET", "/api/dashboard"), ("GET", "/dash.js"),
                             ("POST", "/api/panic")):
            self.assertEqual(self.call(method, path, b"" if method == "POST" else None)[0], 404, path)


class DashPageTests(unittest.TestCase):
    HTML = (ROOT / "web" / "dash.html").read_text()
    JS = (ROOT / "web" / "dash.js").read_text()
    CSS = (ROOT / "web" / "dash.css").read_text()

    def test_no_inline_script_style_handler_or_external_address(self):
        self.assertEqual(self.HTML.count("<script"), 1)
        self.assertNotIn("<style", self.HTML)
        self.assertNotRegex(self.HTML, r"\son[a-z]+=")
        self.assertNotRegex(self.HTML, r"\sstyle=")
        for text in (self.HTML, self.JS, self.CSS):
            self.assertNotRegex(text, r"https?://")
        self.assertNotIn("url(", self.CSS)

    def test_the_script_writes_text_only_and_keeps_the_token_out_of_storage(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                    "sessionStorage", "document.cookie"):
            self.assertNotIn(bad, self.JS)

    def test_the_token_field_is_a_password_field_without_autofill(self):
        self.assertIn('type="password"', self.HTML)
        self.assertIn('autocomplete="off"', self.HTML)


class TokenScriptTests(unittest.TestCase):
    def run_script(self, env_file):
        env = {**os.environ, "LEASH_ARENA_ENV": str(env_file)}
        return subprocess.run(["sh", str(ROOT / "scripts" / "arena-panic-token.sh")], capture_output=True,
                              text=True, env=env)

    def test_it_prints_the_token_once_and_stores_only_its_hash(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sub" / "arena.env"
            p = self.run_script(f)
            self.assertEqual(p.returncode, 0, p.stderr)
            token = p.stdout.strip().splitlines()[-1]
            text = f.read_text()
            self.assertNotIn(token, text)
            self.assertEqual(text.strip(), "LEASH_ARENA_PANIC_SHA256=" + hashlib.sha256(token.encode()).hexdigest())
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            again = self.run_script(f)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(f.read_text(), text)


if __name__ == "__main__":
    unittest.main()
