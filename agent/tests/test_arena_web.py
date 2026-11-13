import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from leash_arena.server import DEFAULT_WEB_DIR, DASH_STATIC, SECURITY_HEADERS, STATIC

WEB = DEFAULT_WEB_DIR
HTML = (WEB / "index.html").read_text()
JS = (WEB / "arena.js").read_text()
CSS = (WEB / "arena.css").read_text()


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.ids, self.attrs = [], set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            self.attrs.append((tag, name, value))
            if name == "id":
                self.ids.add(value)


PAGE = Collect()
PAGE.feed(HTML)


class PageTests(unittest.TestCase):
    def test_the_server_serves_exactly_the_page_files(self):
        self.assertEqual({name for name, _ in {**STATIC, **DASH_STATIC}.values()}, {p.name for p in WEB.iterdir()})

    def test_the_page_has_no_inline_script_style_or_event_handler(self):
        self.assertEqual(PAGE.tags.count("script"), 1)
        self.assertEqual(PAGE.tags.count("style"), 0)
        names = [name for _, name, _ in PAGE.attrs]
        self.assertFalse([n for n in names if n.startswith("on")])
        self.assertNotIn("style", names)
        self.assertNotIn("srcdoc", names)
        self.assertFalse([a for a in PAGE.attrs if a[0] == "script" and a[1] != "defer" and a[1] != "src"])

    def test_the_page_loads_only_its_own_two_files(self):
        links = [(t, n, v) for t, n, v in PAGE.attrs if n in ("src", "href") and t in ("script", "link")]
        self.assertEqual(sorted(v for _, _, v in links), ["/arena.css", "/arena.js"])

    def test_the_page_has_no_form_action_iframe_or_external_address(self):
        self.assertNotIn("iframe", PAGE.tags)
        self.assertNotIn("object", PAGE.tags)
        self.assertNotIn("embed", PAGE.tags)
        self.assertNotIn("action", [n for _, n, _ in PAGE.attrs])
        for text in (HTML, CSS):
            self.assertNotRegex(text, r"https?://")
        self.assertNotIn("@import", CSS)
        self.assertNotIn("url(", CSS)

    def test_the_page_basics_for_phones_and_keyboards(self):
        self.assertIn('<html lang="en">', HTML)
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', HTML)
        self.assertIn("prefers-color-scheme: light", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn('for="message"', HTML)
        self.assertIn('maxlength="500"', HTML)

    def test_every_id_the_script_reads_exists_in_the_page(self):
        used = set(re.findall(r'\$\("([a-z-]+)"\)', JS))
        used |= {name + "-meta" for name in ("fortress", "loose")}
        self.assertFalse(used - PAGE.ids, used - PAGE.ids)

    def test_the_two_tier_values_in_the_page_are_the_ones_the_server_knows(self):
        values = [v for t, n, v in PAGE.attrs if t == "input" and n == "value"]
        self.assertEqual(values, ["fortress", "loose"])


class ScriptTests(unittest.TestCase):
    def test_every_example_button_has_a_prompt_in_the_script(self):
        names = [v for t, n, v in PAGE.attrs if t == "button" and n == "data-prompt"]
        self.assertTrue(names)
        for name in names:
            self.assertRegex(JS, r"\b" + re.escape(name) + r": function")

    def test_no_way_to_turn_text_into_markup_or_code(self):
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                       "setTimeout(\"", "setTimeout('", "setInterval(\"", "createContextualFragment",
                       "DOMParser", "srcdoc", "javascript:", "localStorage", "sessionStorage", "document.cookie",
                       "XMLHttpRequest", "WebSocket", "import(", "postMessage"):
            self.assertNotIn(banned, JS, banned)

    def test_outcome_lookups_ignore_inherited_keys(self):
        self.assertIn("hasOwnProperty", JS)
        self.assertNotRegex(JS, r"OUTCOMES\[[a-z.]+\]\s*\|\|")
        self.assertEqual(len(re.findall(r"OUTCOMES\[", JS)), 1)  # only the guarded helper indexes it
        self.assertNotRegex(JS, r"GATES\[[a-zA-Z]+\]\s*\|\|")  # the gate table is read with the same guard
        self.assertIn("hasOwnProperty.call(GATES", JS)

    def test_the_panic_page_says_to_check_the_status_when_the_answer_drops(self):
        dash_js = (DEFAULT_WEB_DIR / "dash.js").read_text()
        self.assertIn("check the status", dash_js)

    def test_text_reaches_the_page_through_text_content_only(self):
        self.assertIn(".textContent", JS)
        self.assertNotRegex(JS, r"\.(innerText|html)\s*=")

    def test_the_script_calls_only_its_own_origin(self):
        calls = re.findall(r'request\("(/[^"]*)"', JS)
        self.assertEqual(sorted(set(calls)), ["/api/attempt", "/api/feed?limit=20", "/api/stats"])
        self.assertNotRegex(JS, r'fetch\("https?:')

    def test_the_only_outside_address_is_the_explorer_and_links_are_checked(self):
        urls = re.findall(r'https?://[^"\s)]+', JS)
        self.assertEqual(urls, ["https://testnet.nearblocks.io/txns/"])
        self.assertIn("TX_ID.test(txId)", JS)
        self.assertIn('link.rel = "noopener noreferrer"', JS)

    def test_the_api_address_comes_from_one_meta_tag_and_must_be_a_plain_https_origin(self):
        self.assertIn(("meta", "name", "leash-api"), PAGE.attrs)
        self.assertIn(("meta", "content", ""), PAGE.attrs)  # the repository copy means "same address"
        self.assertIn("fetch(API_BASE + path, options)", JS)
        self.assertIn('API_ORIGIN.test(value || "") ? value : ""', JS)
        self.assertIn("https:\\/\\/", JS)
        self.assertEqual(len(re.findall(r"fetch\(", JS)), 1)

    def test_the_request_sends_no_cookies(self):
        self.assertIn('options.credentials = "omit"', JS)

    def test_the_attempt_body_has_exactly_the_two_fields_the_server_wants(self):
        self.assertIn("JSON.stringify({ tier: tier, message: message })", JS)

    def test_the_script_uses_no_inline_style_writes(self):
        self.assertNotIn(".style", JS)
        self.assertNotIn("setAttribute(\"style\"", JS)

    def test_the_security_policy_matches_what_the_page_needs(self):
        csp = SECURITY_HEADERS["Content-Security-Policy"]
        self.assertIn("img-src 'none'", csp)
        self.assertNotRegex(HTML + CSS + JS, r"<img|background-image|\.png|\.svg")


if __name__ == "__main__":
    unittest.main()
