import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vercel_prepare", ROOT / "scripts" / "vercel_prepare.py")
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)

ORIGIN = "https://api.example.org"


class VercelPrepare(unittest.TestCase):
    def build(self, origin=ORIGIN):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "site"
        vp.build(origin, out)
        return out

    def test_the_page_names_the_api_and_nothing_else_changes(self):
        out = self.build()
        page = (out / "index.html").read_text()
        original = (ROOT / "web" / "index.html").read_text()
        self.assertEqual(page, original.replace('content=""', f'content="{ORIGIN}"'))
        for name in ("arena.js", "arena.css"):
            self.assertEqual((out / name).read_bytes(), (ROOT / "web" / name).read_bytes())

    def test_the_policy_allows_only_that_origin_for_requests(self):
        config = json.loads((self.build() / "vercel.json").read_text())
        headers = {h["key"]: h["value"] for h in config["headers"][0]["headers"]}
        policy = headers["Content-Security-Policy"]
        self.assertIn(f"connect-src {ORIGIN};", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe", policy)
        self.assertNotIn("*", policy)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(config["headers"][0]["source"], "/(.*)")

    def test_a_bad_origin_is_refused(self):
        for bad in ("http://api.example.org", "https://api.example.org/", "https://api.example.org/x", "*",
                    "https://API.example.org", "api.example.org", "https://a b.example", "https://x.example; script-src *"):
            with self.assertRaises(SystemExit, msg=bad):
                self.build(bad)


if __name__ == "__main__":
    unittest.main()
