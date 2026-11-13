import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

LANDING = Path(__file__).resolve().parents[2] / "landing"
HTML = (LANDING / "index.html").read_text()
CSS = (LANDING / "landing.css").read_text()
ARENA = "https://leash-arena.taila4b466.ts.net:8443/"


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, n, v) for n, v in attrs]


PAGE = Collect()
PAGE.feed(HTML)


class LandingTests(unittest.TestCase):
    def test_the_folder_holds_exactly_the_site_files(self):
        self.assertEqual({p.name for p in LANDING.iterdir()}, {"index.html", "landing.css", "vercel.json"})

    def test_the_page_has_no_script_inline_style_handler_or_image(self):
        for banned in ("script", "style", "img", "iframe", "form", "object", "embed"):
            self.assertNotIn(banned, PAGE.tags)
        names = [n for _, n, _ in PAGE.attrs]
        self.assertFalse([n for n in names if n.startswith("on")])
        self.assertNotIn("style", names)
        self.assertNotIn("url(", CSS)
        self.assertNotIn("@import", CSS)
        self.assertNotRegex(CSS, r"https?://")

    def test_the_only_stylesheet_is_its_own_and_the_basics_hold(self):
        links = [v for t, n, v in PAGE.attrs if t == "link" and n == "href"]
        self.assertEqual(links, ["/landing.css"])
        self.assertIn('<html lang="en">', HTML)
        self.assertIn("prefers-color-scheme: light", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn("prefers-reduced-motion", CSS)

    def test_the_arena_button_opens_the_arena_address_and_links_stay_on_two_hosts(self):
        hrefs = [v for t, n, v in PAGE.attrs if t == "a" and n == "href"]
        self.assertIn(ARENA, hrefs)
        for href in hrefs:
            self.assertTrue(href == "/" or href == ARENA or href.startswith("https://github.com/MusaAis/leash"), href)

    def test_the_page_names_no_feature_before_its_tag_and_no_process_words(self):
        text = HTML.lower()
        for word in ("cooldown", "hash chain", "would_spend", "chain signatures", "tee", "private ai", "reported", "untested", "pending"):
            self.assertIsNone(re.search(r"\b" + re.escape(word) + r"\b", text), word)

    def test_the_headers_allow_no_script_and_no_network(self):
        config = json.loads((LANDING / "vercel.json").read_text())
        headers = {h["key"]: h["value"] for h in config["headers"][0]["headers"]}
        policy = headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("script-src", policy)
        self.assertNotIn("connect-src", policy)
        self.assertNotIn("unsafe", policy)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
