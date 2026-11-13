"""The pause-only Telegram bot: one owner, a confirm tap, and a key that can only call `pause_all` and `pause_agent`.

Standard library only. It runs as its own process with its own settings file, apart from the Arena server."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable, Mapping

from leash_agent.chain import ChainError, clean_text, parse_call_output, run_near
from leash_agent.config import EnvFileError, load_env_file
from leash_agent.keys import fetch_keys
from leash_agent.panic import PANIC_METHODS
from leash_agent.redact import scrub

from .live import PREFIX, LiveError
from .tiers import NETWORK

API = "https://api.telegram.org/bot"
LONG_POLL_SECONDS = 30
CONFIRM_SECONDS = 60.0
MAX_PAUSES_PER_HOUR = 3
PAUSE_GAS = "30 Tgas"
CALLBACK = re.compile(r"^(ok|no):([0-9a-f]{16})$")
TOKEN = re.compile(r"^[0-9]{5,15}:[A-Za-z0-9_-]{30,60}$")


class BotError(Exception):
    """A Telegram call failed. The message never contains the bot token."""


class BotSettings:
    def __init__(self, token: str, owner_id: int, fortress: str, loose: str, state_dir: Path,
                 key_fortress: str, key_loose: str) -> None:
        self.token, self.owner_id, self.state_dir = token, owner_id, state_dir
        self.prefixes = {"fortress": fortress, "loose": loose}
        self.key_files = {"fortress": key_fortress, "loose": key_loose}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BotSettings":
        env = os.environ if env is None else env
        token, owner = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("LEASH_BOT_USER_ID", "")
        if not TOKEN.match(token):
            raise LiveError("TELEGRAM_BOT_TOKEN is missing or is not a bot token")
        if not (owner.isascii() and owner.isdigit() and 0 < int(owner) < 2**53):
            raise LiveError("LEASH_BOT_USER_ID must be the owner's numeric Telegram id")
        fortress, loose = env.get("LEASH_ARENA_FORTRESS", "arenaf"), env.get("LEASH_ARENA_LOOSE", "arenal")
        if not (PREFIX.match(fortress) and PREFIX.match(loose)) or fortress == loose:
            raise LiveError("the two tier prefixes must be different lower-case names")
        state = Path(env.get("LEASH_ARENA_DIR") or Path.home() / ".leash" / "arena")
        return cls(token, int(owner), fortress, loose, state, env.get("LEASH_BOT_PAUSE_KEY_FORTRESS", ""),
                   env.get("LEASH_BOT_PAUSE_KEY_LOOSE", ""))

    def owner_account(self, tier: str) -> str:
        return f"{self.prefixes[tier]}-owner.testnet"

    def vault(self, tier: str) -> str:
        return f"{self.prefixes[tier]}-vault.testnet"


def key_problem(path: str, owner: str, vault: str, fetch: Callable[[str, str], object] = fetch_keys,
                online: bool = True, info: dict | None = None) -> str | None:
    """None when the file is this owner's key, readable only by its user, and (online) a pause-only key on the vault."""
    if not path:
        return "no key file path is set"
    try:
        mode = Path(path).stat().st_mode & 0o777
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return "the key file is missing or is not a key file"
    if mode & 0o077:
        return "the key file must be mode 0600"
    public = data.get("public_key") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and isinstance(public, str) and isinstance(data.get("private_key"), str)):
        return "the key file is missing or is not a key file"
    if "account_id" in data and data["account_id"] != owner:  # the CLI's folder files carry no account name
        return f"the key file is not for {owner}"
    if not online:
        return None
    try:
        keys = fetch(owner, NETWORK)["result"]["keys"]  # type: ignore[index]
        entry = next(k for k in keys if k["public_key"] == public)
        call = entry["access_key"]["permission"]["FunctionCall"]
    except (ChainError, KeyError, TypeError, StopIteration):
        return "this key is not a function-call key on the owner account"
    if call.get("receiver_id") != vault or sorted(call.get("method_names") or []) != sorted(PANIC_METHODS):
        return f"this key can do more than {', '.join(PANIC_METHODS)} on {vault}"
    allowance = call.get("allowance")
    if allowance is not None:
        try:
            left = int(allowance)
        except (TypeError, ValueError):
            return "the key's gas allowance cannot be read"
        if info is not None:
            info["allowance_near"] = f"{left // 10**24}.{left % 10**24 // 10**20:04d}"
        if left <= 0:
            return "the key's gas allowance is used up"
    return None


class Telegram:
    """The few Bot API calls the bot needs, over HTTPS with long polling."""

    def __init__(self, token: str, opener: Callable = urllib.request.urlopen) -> None:
        self._url, self._opener = f"{API}{token}/", opener

    def call(self, method: str, params: dict, timeout: float = 15.0) -> object:
        request = urllib.request.Request(self._url + method, data=json.dumps(params).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener(request, timeout=timeout) as resp:
                data = json.loads(resp.read(2_000_000).decode())
        except (urllib.error.URLError, OSError, ValueError):
            raise BotError(f"Telegram call {method} failed") from None  # the URL holds the token: say nothing about it
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise BotError(f"Telegram call {method} was refused")
        return data.get("result")

    def updates(self, offset: int | None, timeout: int) -> list:
        params: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        result = self.call("getUpdates", params, timeout=timeout + 10)
        return result if isinstance(result, list) else []

    def send(self, chat_id: int, text: str, keyboard: list | None = None) -> None:
        params: dict = {"chat_id": chat_id, "text": text}
        if keyboard:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        self.call("sendMessage", params)

    def answer(self, callback_id: str, text: str) -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class PauseBot:
    """Turns Telegram updates into at most one pause, and only for the owner."""

    def __init__(self, settings: BotSettings, pause: Callable[[], dict], client, clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = lambda text: print(text, file=sys.stderr, flush=True)) -> None:
        self.owner, self.pause, self.client, self.clock, self.log = settings.owner_id, pause, client, clock, log
        self._open_buttons: dict[str, float] = {}
        self._done: deque[float] = deque()
        self.ignored = 0

    def _is_owner(self, user: object, chat: object) -> bool:
        return isinstance(user, dict) and isinstance(chat, dict) and _int(user.get("id")) == self.owner \
            and _int(chat.get("id")) == self.owner and chat.get("type") == "private"

    def handle(self, update: object) -> None:
        if not isinstance(update, dict):
            return
        if isinstance(update.get("message"), dict):
            msg = update["message"]
            if not self._is_owner(msg.get("from"), msg.get("chat")):
                return self._ignore()
            if msg.get("text") == "/pause":
                self._ask()
            return
        if isinstance(update.get("callback_query"), dict):
            cb = update["callback_query"]
            message = cb.get("message") if isinstance(cb.get("message"), dict) else {}
            if not self._is_owner(cb.get("from"), message.get("chat")) or not isinstance(cb.get("id"), str):
                return self._ignore()
            self._tap(cb["id"], cb.get("data"))

    def _ignore(self) -> None:
        self.ignored += 1
        self.log("bot: ignored an update from someone else")  # no id, no text

    def _ask(self) -> None:
        now = self.clock()
        self._open_buttons = {}  # one open question at a time; an older button stops working
        nonce = secrets.token_hex(8)
        self._open_buttons[nonce] = now + CONFIRM_SECONDS
        self.client.send(self.owner, "Pause the Arena? This stops new attempts and pauses both vaults. "
                         "Tap within a minute.", [[{"text": "Pause now", "callback_data": f"ok:{nonce}"},
                                                   {"text": "Cancel", "callback_data": f"no:{nonce}"}]])

    def _tap(self, callback_id: str, data: object) -> None:
        match = CALLBACK.match(data) if isinstance(data, str) else None
        now = self.clock()
        expires = self._open_buttons.pop(match.group(2), None) if match else None
        if not match or expires is None or expires <= now:
            return self.client.answer(callback_id, "That button has expired. Send /pause again.")
        if match.group(1) == "no":
            return self.client.answer(callback_id, "Cancelled.")
        while self._done and self._done[0] <= now - 3600:
            self._done.popleft()
        if len(self._done) >= MAX_PAUSES_PER_HOUR:
            return self.client.answer(callback_id, "Too many pauses this hour.")
        try:
            result = self.pause()  # the work first: a slow or failing acknowledgement must not hold it back
        except Exception as exc:
            self.log(f"bot: pause failed ({type(exc).__name__})")  # the class only, never the text
            result = {"stopped": False, "vaults": []}
        try:
            self.client.answer(callback_id, "Pausing.")
        except BotError as exc:
            self.log(f"bot: {exc}")
        paused = any(v.get("ok") for v in result.get("vaults", []))
        if paused:
            self._done.append(now)  # only a tap that paused a vault counts against the hourly limit
        lines = ["New attempts are stopped." if result.get("stopped") else "The stop flag could not be written."]
        lines += [f"{v['tier']}: {'paused' if v['ok'] else 'NOT paused. ' + v['detail']}" for v in result.get("vaults", [])]
        self.log("bot: pause run, " + ", ".join(f"{v['tier']}={'ok' if v['ok'] else 'failed'}" for v in result.get("vaults", [])))
        if not paused:
            lines.append("Nothing was paused. Send /pause to try again.")
        self.client.send(self.owner, "\n".join(lines))


def make_pause(settings: BotSettings, runner: Callable[[list[str]], str] = run_near) -> Callable[[], dict]:
    """Stop flag first, then `pause_all` on each vault, signed by that tier's pause key file."""
    def pause() -> dict:
        try:
            settings.state_dir.mkdir(parents=True, exist_ok=True)
            (settings.state_dir / "stop").touch()
            stopped = True
        except OSError:
            stopped = False
        vaults = []
        for tier in ("fortress", "loose"):
            args = ["near", "contract", "call-function", "as-transaction", settings.vault(tier), "pause_all",
                    "json-args", "{}", "prepaid-gas", PAUSE_GAS, "attached-deposit", "0 NEAR",
                    "sign-as", settings.owner_account(tier), "network-config", NETWORK,
                    "sign-with-access-key-file", settings.key_files[tier], "send"]
            try:
                out = parse_call_output(runner(args))
                seen = any(e.get("event") == "paused_all" for e in out.events)
                vaults.append({"tier": tier, "ok": seen, "detail": "" if seen else "no paused_all event in the answer"})
            except (ChainError, ValueError) as exc:
                vaults.append({"tier": tier, "ok": False, "detail": clean_text(scrub(str(exc)), 120)})
        return {"stopped": stopped, "vaults": vaults}
    return pause


def run(client, bot: PauseBot, should_stop: Callable[[], bool] = lambda: False,
        sleep: Callable[[float], None] = time.sleep) -> None:
    """Long polling. Updates that waited while the bot was off are dropped first, so an old tap never fires."""
    offset: int | None = None
    wait = 5.0
    while not should_stop():
        try:
            if offset is None:
                old = client.updates(-1, 0)
                offset = max((u["update_id"] for u in old if isinstance(u, dict) and _int(u.get("update_id")) is not None),
                             default=-1) + 1
                continue
            for update in client.updates(offset, LONG_POLL_SECONDS):
                if isinstance(update, dict) and _int(update.get("update_id")) is not None:
                    offset = max(offset, update["update_id"] + 1)
                bot.handle(update)
            wait = 5.0
        except BotError as exc:
            bot.log(f"bot: {exc}")
            sleep(wait)
            wait = min(wait * 2, 60.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leash_arena.bot", description="Pause-only Telegram bot for the Arena vaults")
    parser.add_argument("--check", action="store_true", help="read the settings and the key files, print, do not poll")
    args = parser.parse_args(argv)
    try:
        load_env_file(Path(os.environ.get("LEASH_BOT_ENV") or Path.home() / ".leash" / "bot.env"))
        settings = BotSettings.from_env()
    except (LiveError, EnvFileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    infos: dict[str, dict] = {t: {} for t in settings.key_files}
    problems = {t: key_problem(settings.key_files[t], settings.owner_account(t), settings.vault(t), info=infos[t])
                for t in settings.key_files}
    for tier, problem in problems.items():
        extra = f" (gas allowance {infos[tier]['allowance_near']} NEAR)" if infos[tier].get("allowance_near") else ""
        print(f"pause key {tier}: {problem or 'ok'}{extra if not problem else ''}")
    print(f"bot: owner id set; vaults {settings.vault('fortress')}, {settings.vault('loose')}; network {NETWORK}")
    if any(problems.values()):
        print("error: a pause key is not usable; the bot does not start", file=sys.stderr)
        return 1
    if args.check:
        return 0
    bot = PauseBot(settings, make_pause(settings), Telegram(settings.token))
    try:
        run(Telegram(settings.token), bot)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
