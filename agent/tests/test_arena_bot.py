import io
import json
import os
import re
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from leash_agent.chain import ChainError
from leash_arena import bot
from leash_arena.bot import BotError, BotSettings, PauseBot, Telegram, key_problem, make_pause, run
from leash_arena.live import LiveError

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OWNER = 4242
TX = "8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA"
EVENT = 'EVENT_JSON:{"standard":"leash","version":"1.0.0","event":"paused_all","data":[{}]}'
OUT = f"{EVENT}\n@@LEASH-STDOUT@@\nTransaction ID: {TX}\n"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "LEASH_BOT_USER_ID": str(OWNER)}


def settings(tmp="/tmp/x", env=None):
    return BotSettings.from_env({**ENV, "LEASH_ARENA_DIR": tmp, "LEASH_BOT_PAUSE_KEY_FORTRESS": "/k/f.json",
                                 "LEASH_BOT_PAUSE_KEY_LOOSE": "/k/l.json", **(env or {})})


class FakeClient:
    def __init__(self):
        self.sent, self.answers, self.queue, self.calls = [], [], [], []

    def send(self, chat_id, text, keyboard=None):
        self.sent.append((chat_id, text, keyboard))

    def answer(self, callback_id, text):
        self.answers.append((callback_id, text))

    def updates(self, offset, timeout):
        self.calls.append((offset, timeout))
        item = self.queue.pop(0) if self.queue else []
        if isinstance(item, Exception):
            raise item
        return item


def message(user=OWNER, chat=OWNER, text="/pause", chat_type="private"):
    return {"update_id": 1, "message": {"from": {"id": user}, "chat": {"id": chat, "type": chat_type}, "text": text}}


def tap(data, user=OWNER, chat=OWNER, chat_type="private", cid="cb1"):
    return {"update_id": 2, "callback_query": {"id": cid, "from": {"id": user}, "data": data,
                                               "message": {"chat": {"id": chat, "type": chat_type}}}}


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class BotCase(unittest.TestCase):
    def setUp(self):
        self.client, self.clock, self.logs, self.paused = FakeClient(), Clock(), [], []
        def pause():
            self.paused.append(1)
            return {"stopped": True, "vaults": [{"tier": "fortress", "ok": True, "detail": ""},
                                                {"tier": "loose", "ok": False, "detail": "no paused_all event in the answer"}]}
        self.bot = PauseBot(settings(), pause, self.client, self.clock, self.logs.append)

    def ask(self):
        self.bot.handle(message())
        return re.search(r"ok:([0-9a-f]{16})", json.dumps(self.client.sent[-1][2])).group(1)


class SettingsTests(unittest.TestCase):
    def test_good_settings(self):
        s = settings()
        self.assertEqual((s.owner_id, s.vault("loose"), s.owner_account("fortress")),
                         (OWNER, "arenal-vault.testnet", "arenaf-owner.testnet"))

    def test_bad_values_are_refused_and_the_token_is_not_printed(self):
        for env in ({"TELEGRAM_BOT_TOKEN": "x"}, {"TELEGRAM_BOT_TOKEN": ""}, {"LEASH_BOT_USER_ID": "abc"},
                    {"LEASH_BOT_USER_ID": "-5"}, {"LEASH_BOT_USER_ID": "0"}, {"LEASH_BOT_USER_ID": ""},
                    {"LEASH_ARENA_FORTRESS": "A b"}, {"LEASH_ARENA_LOOSE": "arenaf"}):
            with self.assertRaises(LiveError, msg=env) as ctx:
                BotSettings.from_env({**ENV, **env})
            self.assertNotIn(TOKEN, str(ctx.exception))


class KeyTests(unittest.TestCase):
    def keyfile(self, account="arenaf-owner.testnet", mode=0o600, public="ed25519:PUB"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "k.json"
        path.write_text(json.dumps({"account_id": account, "public_key": public, "private_key": "ed25519:SECRET"}))
        os.chmod(path, mode)
        return str(path)

    def keys(self, receiver="arenaf-vault.testnet", methods=("pause_all", "pause_agent"), full=False):
        perm = "FullAccess" if full else {"FunctionCall": {"receiver_id": receiver, "method_names": list(methods)}}
        return lambda account, network: {"result": {"keys": [{"public_key": "ed25519:PUB", "access_key": {"permission": perm}}]}}

    def check(self, path, fetch, online=True):
        return key_problem(path, "arenaf-owner.testnet", "arenaf-vault.testnet", fetch, online)

    def test_a_pause_only_key_passes(self):
        self.assertIsNone(self.check(self.keyfile(), self.keys()))

    def test_a_full_access_key_is_refused(self):
        self.assertIn("function-call", self.check(self.keyfile(), self.keys(full=True)))

    def test_a_key_that_can_do_more_or_reach_another_vault_is_refused(self):
        self.assertIn("more than", self.check(self.keyfile(), self.keys(methods=("pause_all", "pause_agent", "withdraw"))))
        self.assertIn("more than", self.check(self.keyfile(), self.keys(methods=("pause_all",))))
        self.assertIn("more than", self.check(self.keyfile(), self.keys(receiver="arenal-vault.testnet")))

    def test_file_problems(self):
        self.assertIn("0600", self.check(self.keyfile(mode=0o664), self.keys()))
        self.assertIn("not for", self.check(self.keyfile(account="other.testnet"), self.keys()))
        self.assertIn("no key file", self.check("", self.keys()))
        self.assertIn("missing", self.check("/nonexistent/k.json", self.keys()))

    def test_an_unlisted_key_or_a_failed_read_is_refused(self):
        self.assertIn("function-call", self.check(self.keyfile(public="ed25519:OTHER"), self.keys()))
        def down(account, network):
            raise ChainError("down")
        self.assertIn("function-call", self.check(self.keyfile(), down))

    def test_offline_checks_the_file_only(self):
        self.assertIsNone(self.check(self.keyfile(), None, online=False))


class TelegramTests(unittest.TestCase):
    class Resp:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return self.data

    def test_calls_go_to_the_bot_url_with_json_and_no_token_in_the_body(self):
        seen = {}
        def opener(request, timeout):
            seen.update(url=request.full_url, body=json.loads(request.data), timeout=timeout)
            return self.Resp(b'{"ok":true,"result":[{"update_id":5}]}')
        client = Telegram(TOKEN, opener)
        self.assertEqual(client.updates(7, 30), [{"update_id": 5}])
        self.assertEqual(seen["url"], f"https://api.telegram.org/bot{TOKEN}/getUpdates")
        self.assertEqual(seen["body"], {"timeout": 30, "allowed_updates": ["message", "callback_query"], "offset": 7})
        self.assertNotIn(TOKEN, json.dumps(seen["body"]))
        self.assertGreater(seen["timeout"], 30)

    def test_errors_never_carry_the_token(self):
        def boom(request, timeout):
            raise OSError(f"cannot reach https://api.telegram.org/bot{TOKEN}/getUpdates")
        with self.assertRaises(BotError) as ctx:
            Telegram(TOKEN, boom).updates(None, 0)
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        with self.assertRaises(BotError) as ctx:
            Telegram(TOKEN, lambda r, timeout: self.Resp(b'{"ok":false,"description":"x"}')).send(1, "hi")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_send_and_answer_bodies(self):
        bodies = []
        def opener(request, timeout):
            bodies.append((request.full_url.rsplit("/", 1)[1], json.loads(request.data)))
            return self.Resp(b'{"ok":true,"result":true}')
        client = Telegram(TOKEN, opener)
        client.send(5, "t", [[{"text": "a", "callback_data": "x"}]])
        client.answer("c", "done")
        self.assertEqual(bodies[0], ("sendMessage", {"chat_id": 5, "text": "t",
                                                      "reply_markup": {"inline_keyboard": [[{"text": "a", "callback_data": "x"}]]}}))
        self.assertEqual(bodies[1], ("answerCallbackQuery", {"callback_query_id": "c", "text": "done"}))


class FlowTests(BotCase):
    def test_pause_asks_with_two_buttons_whose_data_fits_the_limit(self):
        self.bot.handle(message())
        chat, text, keyboard = self.client.sent[0]
        self.assertEqual(chat, OWNER)
        buttons = keyboard[0]
        self.assertEqual([b["text"] for b in buttons], ["Pause now", "Cancel"])
        for b in buttons:
            self.assertRegex(b["callback_data"], r"^(ok|no):[0-9a-f]{16}$")
            self.assertLessEqual(len(b["callback_data"].encode()), 64)
        self.assertEqual(self.paused, [])

    def test_the_confirm_tap_pauses_once_and_reports_each_vault(self):
        nonce = self.ask()
        self.bot.handle(tap(f"ok:{nonce}"))
        self.assertEqual(len(self.paused), 1)
        self.assertEqual(self.client.answers[-1][1], "Pausing.")
        report = self.client.sent[-1][1]
        self.assertIn("fortress: paused", report)
        self.assertIn("loose: NOT paused", report)
        self.bot.handle(tap(f"ok:{nonce}"))  # the same button again
        self.assertEqual(len(self.paused), 1)
        self.assertIn("expired", self.client.answers[-1][1])

    def test_cancel_and_expiry_do_not_pause(self):
        nonce = self.ask()
        self.bot.handle(tap(f"no:{nonce}"))
        self.assertEqual((self.paused, self.client.answers[-1][1]), ([], "Cancelled."))
        nonce = self.ask()
        self.clock.now += bot.CONFIRM_SECONDS + 1
        self.bot.handle(tap(f"ok:{nonce}"))
        self.assertEqual(self.paused, [])
        self.assertIn("expired", self.client.answers[-1][1])

    def test_a_new_question_voids_the_old_button(self):
        old = self.ask()
        self.ask()
        self.bot.handle(tap(f"ok:{old}"))
        self.assertEqual(self.paused, [])

    def test_a_made_up_button_does_nothing(self):
        self.ask()
        for data in ("ok:" + "0" * 16, "ok:zz", "pause", "", None, 5, "ok:" + "a" * 17, "OK:" + "a" * 16):
            self.bot.handle(tap(data))
        self.assertEqual(self.paused, [])

    def test_at_most_three_pauses_an_hour(self):
        for _ in range(bot.MAX_PAUSES_PER_HOUR):
            self.bot.handle(tap(f"ok:{self.ask()}"))
        self.assertEqual(len(self.paused), 3)
        self.bot.handle(tap(f"ok:{self.ask()}"))
        self.assertEqual(len(self.paused), 3)
        self.assertIn("Too many", self.client.answers[-1][1])
        self.clock.now += 3601
        self.bot.handle(tap(f"ok:{self.ask()}"))
        self.assertEqual(len(self.paused), 4)

    def test_other_text_from_the_owner_gets_no_reply_and_no_pause(self):
        for text in ("/resume", "/withdraw", "pause", "/pause now", "hello", None):
            self.bot.handle(message(text=text))
        self.assertEqual((self.client.sent, self.paused), ([], []))


class StrangerTests(BotCase):
    def test_a_stranger_gets_no_reply_no_pause_and_a_log_line_without_content(self):
        nonce = self.ask()
        sent = len(self.client.sent)
        self.bot.handle(message(user=999, chat=999))
        self.bot.handle(tap(f"ok:{nonce}", user=999, chat=999, cid="evil"))
        self.bot.handle(tap(f"ok:{nonce}", user=999))  # a stranger tapping the owner's button in the owner's chat
        self.assertEqual((len(self.client.sent), self.paused, self.client.answers), (sent, [], []))
        self.assertEqual(self.bot.ignored, 3)
        self.assertTrue(all("999" not in line and "pause" not in line.lower() for line in self.logs))

    def test_the_owner_id_in_a_group_or_with_a_wrong_shape_is_ignored(self):
        nonce = self.ask()
        self.bot.handle(message(chat=-100123, chat_type="supergroup"))
        self.bot.handle(message(chat=OWNER, chat_type="group"))
        self.bot.handle(tap(f"ok:{nonce}", chat=-100123, chat_type="supergroup"))
        self.assertEqual(self.paused, [])

    def test_odd_updates_are_ignored(self):
        for update in (None, 5, [], {}, {"message": 5}, {"message": {}}, {"callback_query": {}},
                       {"callback_query": {"id": "x", "from": {"id": OWNER}, "data": "ok:" + "a" * 16}},
                       message(user=str(OWNER)), message(user=True if OWNER == 1 else float(OWNER)),
                       {"message": {"from": {"id": OWNER}, "chat": {"id": OWNER}, "text": "/pause"}}):
            self.bot.handle(update)
        self.assertEqual((self.client.sent, self.paused), ([], []))


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []

    def pause(self, runner):
        return make_pause(settings(self.tmp.name), runner)()

    def test_the_stop_flag_comes_first_then_each_vault_is_called_with_its_key_file(self):
        def runner(args):
            self.calls.append(args)
            self.assertTrue((Path(self.tmp.name) / "stop").exists())
            return OUT
        result = self.pause(runner)
        self.assertEqual([v["ok"] for v in result["vaults"]], [True, True])
        first = self.calls[0]
        self.assertEqual(first[4:6], ["arenaf-vault.testnet", "pause_all"])
        self.assertEqual(first[first.index("sign-as") + 1], "arenaf-owner.testnet")
        self.assertEqual(first[first.index("sign-with-access-key-file") + 1], "/k/f.json")
        self.assertEqual(first[first.index("attached-deposit") + 1], "0 NEAR")
        self.assertNotIn("sign-with-legacy-keychain", first)
        self.assertEqual(self.calls[1][4], "arenal-vault.testnet")

    def test_failures_are_reported_and_secrets_scrubbed(self):
        def runner(args):
            if args[4].startswith("arenaf"):
                raise ChainError(f"near CLI failed: {os.environ['LEASH_ROUTE_RECIPIENT']} nope")
            return "@@LEASH-STDOUT@@\nnothing\n"
        with mock.patch.dict(os.environ, {"LEASH_ROUTE_RECIPIENT": "0xsecretrecipientvalue0000"}):
            result = self.pause(runner)
        self.assertEqual([v["ok"] for v in result["vaults"]], [False, False])
        self.assertNotIn("secretrecipient", json.dumps(result))

    def test_an_unwritable_flag_still_pauses_the_vaults(self):
        path = Path(self.tmp.name) / "file"
        path.write_text("x")
        result = make_pause(settings(str(path) + "/sub"), lambda args: OUT)()
        self.assertFalse(result["stopped"])
        self.assertTrue(all(v["ok"] for v in result["vaults"]))


class RunTests(BotCase):
    def test_old_updates_are_dropped_first_and_the_offset_advances(self):
        self.client.queue = [[{"update_id": 40}, {"update_id": 41}], [message() | {"update_id": 42}]]
        stops = iter([False, False, True])
        run(self.client, self.bot, lambda: next(stops))
        self.assertEqual(self.client.calls[0], (-1, 0))
        self.assertEqual(self.client.calls[1][0], 42)
        self.assertEqual(len(self.client.calls), 2)  # the first round only drops old updates
        self.assertEqual(len(self.client.sent), 1)  # only the new /pause was handled
        self.assertEqual(self.paused, [])

    def test_a_telegram_error_backs_off_and_goes_on(self):
        self.client.queue = [[], BotError("Telegram call getUpdates failed"), BotError("again"), []]
        sleeps, stops = [], iter([False, False, False, False, True])
        run(self.client, self.bot, lambda: next(stops), sleeps.append)
        self.assertEqual(sleeps, [5.0, 10.0])
        self.assertTrue(any("failed" in line for line in self.logs))


class MainTests(unittest.TestCase):
    def run_main(self, argv, problem=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
                **ENV, "LEASH_BOT_ENV": str(Path(tmp) / "none.env"), "LEASH_BOT_PAUSE_KEY_FORTRESS": "/k/f.json",
                "LEASH_BOT_PAUSE_KEY_LOOSE": "/k/l.json", **(env or {})}), \
                mock.patch.object(bot, "key_problem", return_value=problem), redirect_stdout(out), redirect_stderr(err):
            code = bot.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_prints_the_state_and_never_the_token_or_the_owner_id(self):
        code, out, err = self.run_main(["--check"])
        self.assertEqual(code, 0)
        self.assertIn("pause key fortress: ok", out)
        for text in (out, err):
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(str(OWNER), text)

    def test_a_bad_key_stops_the_start(self):
        code, out, err = self.run_main([], problem="this key can do more than pause_agent, pause_all on x")
        self.assertEqual(code, 1)
        self.assertIn("does not start", err)

    def test_missing_settings_stop_the_start_without_the_token(self):
        code, _, err = self.run_main(["--check"], env={"LEASH_BOT_USER_ID": "nope"})
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, err)


if __name__ == "__main__":
    unittest.main()
