import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from leash_agent import callpath, swap
from leash_agent.__main__ import main
from leash_agent.chain import STDOUT_SENTINEL, ChainError, Snapshot
from leash_agent.guardian import GuardianLog
from leash_agent.intents import iso, utc_now
from tests.test_callpath import HEX64, RECIPIENT, make_config, payload, value_after

NEAR = 10**24
OTHER = "cd" * 32
MIN = datetime.timedelta(minutes=1)


def snap(**over):
    base = dict(per_tx_cap=NEAR // 2, daily_cap=NEAR, spent=0, allowlist=(), agent_paused=False, all_paused=False, balance=5 * NEAR)
    base.update(over)
    return Snapshot(**base)


class FakeVault:
    def __init__(self, snapshot=None):
        self.snapshot_value, self.reads, self.approvals, self.view_error = snapshot or snap(), 0, [], None

    def view(self, method, args=None):
        if self.view_error:
            raise self.view_error
        return self.approvals if method == "get_approvals" else []

    def snapshot(self):
        self.reads += 1
        return self.snapshot_value


class Chain:
    """A fake `near`: views answer from the fields, transactions are recorded in `calls`."""

    def __init__(self, balance=5 * NEAR, paused=False, tx_output="ok", routes=None):
        self.balance, self.paused, self.tx_output, self.calls, self.views = balance, paused, tx_output, [], []
        self.routes = routes

    def __call__(self, args):
        if "as-read-only" in args:
            self.views.append(args[5])
            from tests.test_intents import ASSET as route_asset
            stored = self.routes if self.routes is not None else [{"dest_asset": route_asset, "recipient": RECIPIENT}]
            answer = {
                "get_policy": {"per_tx_cap": str(NEAR // 2), "daily_cap": str(NEAR), "spent_in_window": "0", "allowlist": [], "paused": False},
                "is_paused_all": self.paused, "get_available_balance": str(self.balance), "get_routes": stored,
            }.get(args[5], [])
            return STDOUT_SENTINEL + json.dumps(answer)
        self.calls.append(args)
        return self.tx_output + STDOUT_SENTINEL


RESPONSE = {"quote": {"depositAddress": HEX64, "amountIn": str(NEAR // 10)}, "signature": "sig"}


def quote_entry(cfg, now, *, age=5, deadline_in=15, amount=NEAR // 10, deposit=HEX64, route=0, agent=None):
    return {
        "event": "quote_issued", "ts": (now - age * MIN).isoformat(), "agent": agent or cfg.agent, "route_id": route,
        "amount": str(amount), "deposit_address": deposit, "deadline": iso(now + 3 * 24 * 60 * MIN),
        "request_deadline": iso(now + deadline_in * MIN), "signature": "sig", "response": RESPONSE,
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = GuardianLog(Path(self.tmp.name) / "log.jsonl")
        self.cfg, self.now = make_config("mainnet"), utc_now()

    def add(self, entry):
        self.log.append(entry)

    def approve(self, amount="0.1", record=False, vault=None, **kw):
        return swap.prepare_approval(self.cfg, vault or FakeVault(), self.log, 0, amount, record=record, now=self.now, **kw)

    def run_main(self, argv, chain, env=None):
        out, err = io.StringIO(), io.StringIO()
        env = dict({"LEASH_GUARDIAN_LOG": str(self.log.path), "LEASH_ALLOW_MAINNET": "1", "LEASH_ROUTE_RECIPIENT": RECIPIENT}, **(env or {}))
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            code = main(argv, cfg=self.cfg, runner=chain)
        return code, out.getvalue(), err.getvalue()

    def events(self, name):
        return [e for e in self.log.entries() if e.get("event") == name]


class PrepareApproval(Base):
    def test_deposit_account_and_hash_come_from_the_saved_quote(self):
        self.add(quote_entry(self.cfg, self.now))
        args, extra = self.approve()
        data = payload(args)
        self.assertEqual(data["deposit_account"], HEX64)
        self.assertEqual(data["quote_hash"], swap.quote_hash(RESPONSE))
        self.assertEqual(data["amount"], str(NEAR // 10))
        self.assertEqual(value_after(args, "sign-as"), "g.near")
        self.assertIn(swap.quote_hash(RESPONSE), " ".join(extra))

    def test_the_hash_is_sha256_of_sorted_compact_json(self):
        import hashlib
        text = json.dumps(RESPONSE, sort_keys=True, separators=(",", ":"))
        self.assertEqual(swap.quote_hash(RESPONSE), hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(swap.quote_hash({"b": 1, "a": 2}), swap.quote_hash({"a": 2, "b": 1}))

    def test_no_saved_quote_is_refused(self):
        with self.assertRaisesRegex(callpath.CallPathError, "no saved quote"):
            self.approve()
        self.add(quote_entry(self.cfg, self.now, route=3))  # another route
        self.add(quote_entry(self.cfg, self.now, agent="other.near"))  # another agent
        with self.assertRaisesRegex(callpath.CallPathError, "no saved quote"):
            self.approve()

    def test_the_newest_quote_is_used_and_an_amount_mismatch_is_refused(self):
        self.add(quote_entry(self.cfg, self.now, deposit=OTHER, age=9))
        self.add(quote_entry(self.cfg, self.now, deposit=HEX64, age=2))
        self.assertEqual(payload(self.approve()[0])["deposit_account"], HEX64)
        with self.assertRaisesRegex(callpath.CallPathError, "not 0.2 NEAR"):
            self.approve("0.2")

    def test_a_quote_older_than_the_window_is_refused(self):
        self.add(quote_entry(self.cfg, self.now, age=31, deadline_in=15))
        with self.assertRaisesRegex(callpath.CallPathError, "older than 30 minutes"):
            self.approve()
        self.log.path.unlink()
        self.add(quote_entry(self.cfg, self.now, age=29, deadline_in=15))
        self.approve()

    def test_a_passed_or_nearly_passed_deadline_is_refused(self):
        for deadline_in in (-5, 0, 0.5):
            if self.log.path.exists():
                self.log.path.unlink()
            self.add(quote_entry(self.cfg, self.now, deadline_in=deadline_in))
            with self.assertRaisesRegex(callpath.CallPathError, "deadline"):
                self.approve()
            self.assertEqual(self.events("approval_sent"), [])

    def test_expiry_is_the_earlier_of_the_deadline_and_29_minutes_never_30(self):
        for deadline_in, expect in ((10.5, 10), (29.5, 29), (60, 29), (3 * 24 * 60, 29)):
            if self.log.path.exists():
                self.log.path.unlink()
            self.add(quote_entry(self.cfg, self.now, deadline_in=deadline_in))
            args, _ = self.approve()
            expires = int(payload(args)["expires_ns"])
            now_ns = int(self.now.timestamp() * 1_000_000) * 1000
            self.assertEqual(expires - now_ns, expect * 60 * 10**9, deadline_in)
            self.assertLess(expires - now_ns, 30 * 60 * 10**9)
        self.assertEqual(payload(self.approve(max_minutes=5)[0])["expires_ns"], str(now_ns + 5 * 60 * 10**9))

    def test_a_deposit_address_of_the_wrong_shape_is_refused(self):
        for bad in ("AB" * 32, "ab" * 31, "g.near", "", None):
            if self.log.path.exists():
                self.log.path.unlink()
            self.add(quote_entry(self.cfg, self.now, deposit=bad))
            with self.assertRaisesRegex(callpath.CallPathError, "deposit address"):
                self.approve()

    def test_an_approval_is_recorded_only_when_asked_and_only_once_per_quote(self):
        self.add(quote_entry(self.cfg, self.now))
        self.approve(record=False)
        self.assertEqual(self.events("approval_sent"), [])
        self.approve(record=True)
        recorded = self.events("approval_sent")
        self.assertEqual([e["deposit_address"] for e in recorded], [HEX64])
        self.assertEqual(recorded[0]["quote_hash"], swap.quote_hash(RESPONSE))
        with self.assertRaisesRegex(callpath.CallPathError, "already approved"):
            self.approve(record=True)
        self.assertEqual(len(self.events("approval_sent")), 1)

    def test_six_approvals_an_hour_is_the_limit(self):
        for i in range(5):
            self.add({"event": "approval_sent", "ts": (self.now - (10 + i) * MIN).isoformat(), "deposit_address": f"{i:064x}"})
        self.add(quote_entry(self.cfg, self.now))
        self.approve()  # the sixth is allowed
        self.add({"event": "approval_sent", "ts": (self.now - 50 * MIN).isoformat(), "deposit_address": OTHER})
        with self.assertRaisesRegex(callpath.CallPathError, "rate_limit"):
            self.approve()
        self.log.path.unlink()  # approvals older than an hour no longer count
        for i in range(6):
            self.add({"event": "approval_sent", "ts": (self.now - (61 + i) * MIN).isoformat(), "deposit_address": f"{i:064x}"})
        self.add(quote_entry(self.cfg, self.now))
        self.approve()

    def test_fresh_vault_state_decides_and_a_denial_records_no_approval(self):
        self.add(quote_entry(self.cfg, self.now))
        cases = {
            "all_paused": snap(all_paused=True), "agent_paused": snap(agent_paused=True),
            "over_per_tx_cap": snap(per_tx_cap=NEAR // 20), "over_daily_cap": snap(spent=NEAR - 1),
            "insufficient_funds": snap(balance=NEAR // 10),
        }
        for reason, state in cases.items():
            vault = FakeVault(state)
            with self.assertRaisesRegex(callpath.CallPathError, reason):
                self.approve(record=True, vault=vault)
            self.assertEqual(vault.reads, 1, reason)
        self.assertEqual(self.events("approval_sent"), [])
        self.assertEqual([e["reason"] for e in self.events("refused")], list(cases))

    def test_a_damaged_log_fails_closed(self):
        self.log.path.write_text("not json\n")
        with self.assertRaises(Exception):
            self.approve()


class PrepareSpend(Base):
    def approved(self, **over):
        entry = {"event": "approval_sent", "ts": self.now.isoformat(), "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                 "deposit_address": HEX64, "quote_hash": "ab" * 32, "expires_ns": str(int(self.now.timestamp() * 1e6) * 1000 + 10 * 60 * 10**9)}
        entry.update(over)
        self.add(entry)

    def spend(self, amount="0.1", record=False):
        return swap.prepare_spend(self.cfg, self.log, 0, amount, record=record, now=self.now)

    def test_the_deposit_account_is_the_last_approved_quotes(self):
        self.approved(deposit_address=OTHER)
        self.approved()
        args, extra = self.spend()
        self.assertEqual(payload(args), {"route_id": 0, "deposit_account": HEX64, "amount": str(NEAR // 10)})
        self.assertEqual(value_after(args, "sign-as"), "a.near")
        self.assertIn("expires", extra[0])

    def test_no_approval_wrong_amount_and_expired_are_refused(self):
        with self.assertRaisesRegex(callpath.CallPathError, "no approval"):
            self.spend()
        self.approved()
        with self.assertRaisesRegex(callpath.CallPathError, "not 0.2 NEAR"):
            self.spend("0.2")
        self.log.path.unlink()
        self.approved(expires_ns=str(int(self.now.timestamp() * 1e6) * 1000 - 1))
        with self.assertRaisesRegex(callpath.CallPathError, "expired"):
            self.spend()

    def test_a_logged_account_of_the_wrong_shape_is_refused(self):
        self.approved(deposit_address="evil.near")
        with self.assertRaisesRegex(callpath.CallPathError, "expected shape"):
            self.spend()

    def test_the_attempt_is_logged_only_when_sending(self):
        self.approved()
        self.spend(record=False)
        self.assertEqual(self.events("spend_sent"), [])
        self.spend(record=True)
        self.assertEqual(len(self.events("spend_sent")), 1)


class Cli(Base):
    def test_approve_plan_shows_the_deposit_amount_expiry_and_caps_and_records_nothing(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = Chain()
        code, out, err = self.run_main(["intents-approve", "--amount", "0.1"], chain)
        self.assertEqual(code, 0, err)
        for text in (HEX64, "0.1 NEAR", "route 0", "approval expires", "UTC", "per-tx 0.5 NEAR", "daily 1 NEAR", "plan only"):
            self.assertIn(text, out)
        self.assertNotIn(RECIPIENT, out + err)
        self.assertEqual((chain.calls, self.events("approval_sent")), ([], []))

    def test_approve_with_yes_sends_once_and_a_second_run_is_refused(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = Chain(tx_output="EVENT_JSON:" + json.dumps({"standard": "leash", "event": "intent_approved", "data": [{}]}))
        code, out, err = self.run_main(["intents-approve", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(chain.calls), 1)
        self.assertEqual(chain.calls[0][5], "approve_intent")
        self.assertIn("RESULT: DONE", out)
        self.assertEqual(len(self.events("approval_sent")), 1)
        code, _, err = self.run_main(["intents-approve", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 1)
        self.assertIn("already approved", err)
        self.assertEqual(len(chain.calls), 1)

    def test_nothing_is_read_or_recorded_without_the_mainnet_variable(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = Chain()
        with mock.patch.dict(os.environ, {"LEASH_ALLOW_MAINNET": "0"}):
            code, _, err = self.run_main(["intents-approve", "--amount", "0.1", "--yes"], chain, {"LEASH_ALLOW_MAINNET": "0"})
        self.assertEqual(code, 1)
        self.assertIn("LEASH_ALLOW_MAINNET", err)
        self.assertEqual((chain.calls, chain.views, self.events("approval_sent")), ([], [], []))

    def test_a_vault_denial_stops_the_approval(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = Chain(paused=True)
        code, _, err = self.run_main(["intents-approve", "--amount", "0.1", "--yes"], chain)
        self.assertEqual((code, chain.calls, self.events("approval_sent")), (1, [], []))
        self.assertIn("all_paused", err)

    def test_spend_uses_the_approved_deposit_and_a_typed_account_is_refused(self):
        entry = {"event": "approval_sent", "ts": utc_now().isoformat(), "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                 "deposit_address": HEX64, "quote_hash": "ab" * 32, "expires_ns": str((int(utc_now().timestamp()) + 600) * 10**9)}
        self.add(entry)
        chain = Chain()
        code, out, err = self.run_main(["intents-spend", "--amount", "0.1", "--deposit-account", OTHER, "--yes"], chain)
        self.assertEqual((code, chain.calls), (1, []))
        self.assertIn("last approved quote", err)
        code, out, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload(chain.calls[0])["deposit_account"], HEX64)
        self.assertEqual(chain.calls[0][5], "spend_intent")
        for text in (HEX64, "0.1 NEAR", "per-tx 0.5 NEAR", "approval expires"):
            self.assertIn(text, out)

    def test_testnet_keeps_the_manual_deposit_account(self):
        self.cfg = make_config("testnet")
        chain = Chain()
        code, _, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual((code, chain.calls), (1, []))
        self.assertIn("--deposit-account", err)
        code, _, err = self.run_main(["intents-spend", "--amount", "0.1", "--deposit-account", HEX64, "--yes"], chain)
        self.assertEqual((code, len(chain.calls)), (0, 1), err)


class Quote(Base):
    def test_quote_is_refused_on_testnet_and_without_yes(self):
        self.cfg = make_config("testnet")
        code, _, err = self.run_main(["intents-quote", "--amount", "0.1", "--yes"], Chain())
        self.assertEqual(code, 1)
        self.assertIn("no testnet", err)
        self.cfg = make_config("mainnet")
        chain = Chain()
        code, _, err = self.run_main(["intents-quote", "--amount", "0.1"], chain)
        self.assertEqual((code, chain.calls), (1, []))
        self.assertIn("--yes", err)
        with mock.patch.dict(os.environ, {"LEASH_ALLOW_MAINNET": "0"}):
            code, _, err = self.run_main(["intents-quote", "--amount", "0.1", "--yes"], chain, {"LEASH_ALLOW_MAINNET": "0"})
        self.assertEqual(code, 1)
        self.assertIn("LEASH_ALLOW_MAINNET", err)


class QuoteReadsTheStoredRoute(Base):
    def test_a_different_route_in_env_is_refused_before_any_quote(self):
        from tests.test_guardian import FakeOneClick
        from tests.test_intents import ASSET
        self.cfg = make_config("mainnet")
        oneclick = FakeOneClick()
        chain = Chain(routes=[{"dest_asset": ASSET, "recipient": "0xSomeoneElse"}])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"LEASH_GUARDIAN_LOG": str(self.log.path), "LEASH_ALLOW_MAINNET": "1",
                                          "LEASH_ROUTE_RECIPIENT": RECIPIENT, "LEASH_ROUTE_DEST_ASSET": ASSET}), redirect_stdout(out), redirect_stderr(err):
            code = main(["intents-quote", "--amount", "0.1", "--yes"], cfg=self.cfg, runner=chain, oneclick=oneclick)
        self.assertEqual((code, oneclick.quotes), (1, []))
        self.assertIn("differs", err.getvalue())
        self.assertNotIn(RECIPIENT, err.getvalue() + out.getvalue())
        self.assertNotIn("0xSomeoneElse", err.getvalue() + out.getvalue())

    def test_with_no_recipient_in_env_the_stored_route_is_used(self):
        from tests.test_guardian import FakeOneClick
        self.cfg = make_config("mainnet")
        oneclick = FakeOneClick()
        out, err = io.StringIO(), io.StringIO()
        env = {"LEASH_GUARDIAN_LOG": str(self.log.path), "LEASH_ALLOW_MAINNET": "1"}
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            os.environ.pop("LEASH_ROUTE_RECIPIENT", None)
            os.environ.pop("LEASH_ROUTE_DEST_ASSET", None)
            code = main(["intents-quote", "--amount", "0.1", "--yes"], cfg=self.cfg, runner=Chain(), oneclick=oneclick)
        self.assertEqual((code, len(oneclick.quotes)), (0, 1), err.getvalue())
        self.assertEqual(oneclick.quotes[0].body()["recipient"], RECIPIENT)


class EndToEnd(Base):
    def test_quote_then_approve_then_spend_use_one_deposit_address_from_the_log(self):
        from tests.test_guardian import FakeOneClick
        from tests.test_intents import ASSET, DEPOSIT
        chain = Chain()
        env = {"LEASH_ROUTE_DEST_ASSET": ASSET}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, dict({"LEASH_GUARDIAN_LOG": str(self.log.path), "LEASH_ALLOW_MAINNET": "1",
                                               "LEASH_ROUTE_RECIPIENT": RECIPIENT}, **env)), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["intents-quote", "--amount", "0.1", "--yes"], cfg=self.cfg, runner=chain, oneclick=FakeOneClick()), 0, err.getvalue())
            self.assertIn("Nothing was deposited", out.getvalue())
            self.assertNotIn(RECIPIENT, out.getvalue() + err.getvalue())
            self.assertEqual(main(["intents-approve", "--amount", "0.1", "--yes"], cfg=self.cfg, runner=chain), 0, err.getvalue())
            self.assertEqual(main(["intents-spend", "--amount", "0.1", "--yes"], cfg=self.cfg, runner=chain), 0, err.getvalue())
        approve, spend = chain.calls
        self.assertEqual((approve[5], spend[5]), ("approve_intent", "spend_intent"))
        self.assertEqual(payload(approve)["deposit_account"], DEPOSIT)
        self.assertEqual(payload(spend)["deposit_account"], DEPOSIT)
        quoted = self.events("quote_issued")[0]
        self.assertEqual(payload(approve)["quote_hash"], swap.quote_hash(quoted["response"]))
        self.assertEqual([e["event"] for e in self.log.entries() if e["event"] in ("approval_sent", "spend_sent")], ["approval_sent", "spend_sent"])


class OrderedChain(Chain):
    """Checks, when a transaction is sent, that the balance entry is already in the private log."""

    def __init__(self, log, **kw):
        super().__init__(**kw)
        self.log, self.saved_before_send = log, None

    def __call__(self, args):
        if "as-transaction" in args:
            self.saved_before_send = [e for e in self.log.entries() if e.get("event") == "spend_balance_before"]
        return super().__call__(args)


class BalanceAroundSpend(Base):
    def approved(self):
        self.add({"event": "approval_sent", "ts": utc_now().isoformat(), "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                  "deposit_address": HEX64, "quote_hash": "ab" * 32, "expires_ns": str((int(utc_now().timestamp()) + 600) * 10**9)})

    def test_the_balance_is_saved_before_the_transaction_is_sent(self):
        self.approved()
        chain = OrderedChain(self.log, balance=7 * NEAR + 5)
        code, out, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 0, err)
        saved = chain.saved_before_send
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0]["balance"], saved[0]["deposit_address"], saved[0]["amount"]), (str(7 * NEAR + 5), HEX64, str(NEAR // 10)))
        self.assertIn("balance before the spend, saved in the private log: 7.000000000000000000000005 NEAR", out)
        self.assertEqual(oct(self.log.path.stat().st_mode & 0o777), "0o600")

    def test_a_plan_reads_and_saves_nothing(self):
        self.approved()
        chain = Chain()
        code, _, err = self.run_main(["intents-spend", "--amount", "0.1"], chain)
        self.assertEqual((code, chain.calls, self.events("spend_balance_before")), (0, [], []))
        self.assertNotIn("get_available_balance", chain.views)

    def test_an_unreadable_balance_stops_mainnet_and_is_noted_on_testnet(self):
        self.approved()

        class Blind(Chain):
            def __call__(self, args):
                if "as-read-only" in args and args[5] == "get_available_balance":
                    return STDOUT_SENTINEL + "not json {"
                return super().__call__(args)
        chain = Blind()
        code, _, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual((code, chain.calls), (1, []))
        self.assertIn("balance could not be read", err)
        self.cfg = make_config("testnet")
        code, out, err = self.run_main(["intents-spend", "--amount", "0.1", "--deposit-account", HEX64, "--yes"], chain)
        self.assertEqual((code, len(chain.calls)), (0, 1), err)
        self.assertIn("could not be read (testnet)", out)
        self.assertEqual(self.events("spend_balance_before"), [])

    def test_testnet_saves_the_balance_too(self):
        self.cfg = make_config("testnet")
        chain = Chain(balance=3 * NEAR)
        self.run_main(["intents-spend", "--amount", "0.1", "--deposit-account", HEX64, "--yes"], chain)
        self.assertEqual([e["balance"] for e in self.events("spend_balance_before")], [str(3 * NEAR)])

    def entry(self, balance, ts="2026-11-03T08:00:00+00:00"):
        self.add({"event": "spend_balance_before", "ts": ts, "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                  "deposit_address": HEX64, "balance": str(balance)})

    def test_status_prints_before_now_and_the_difference(self):
        self.entry(5 * NEAR)
        code, out, _ = self.run_main(["intents-status"], Chain(balance=5 * NEAR - NEAR // 10 - 2_500_000_000_000_000_000_000))
        self.assertEqual(code, 0)
        for text in ("-- balance around the last spend", "5 NEAR (5000000000000000000000000 yocto)", "difference: -0.1025 NEAR",
                     "(-102500000000000000000000 yocto)", "other transactions in between"):
            self.assertIn(text, out)

    def test_a_zero_difference_says_the_batch_may_still_be_running(self):
        self.entry(5 * NEAR)
        _, out, _ = self.run_main(["intents-status"], Chain(balance=5 * NEAR))
        self.assertIn("difference: +0 NEAR (0 yocto)", out)
        self.assertIn("the batch may still be running", out)

    def test_status_uses_the_newest_entry_of_this_agent_only(self):
        self.entry(1 * NEAR)
        self.entry(2 * NEAR, ts="2026-11-03T09:00:00+00:00")
        self.add({"event": "spend_balance_before", "ts": "x", "agent": "other.near", "balance": str(9 * NEAR)})
        _, out, _ = self.run_main(["intents-status"], Chain(balance=2 * NEAR))
        self.assertIn("2026-11-03T09:00:00", out)
        self.assertNotIn("9000000000000000000000000", out)

    def test_status_without_an_entry_or_with_a_damaged_one_or_an_unreadable_balance(self):
        _, out, _ = self.run_main(["intents-status"], Chain())
        self.assertIn("no saved balance", out)
        self.add({"event": "spend_balance_before", "agent": self.cfg.agent, "balance": "abc"})
        _, out, _ = self.run_main(["intents-status"], Chain())
        self.assertIn("damaged", out)
        self.log.path.unlink()
        self.entry(NEAR)

        class Blind(Chain):
            def __call__(self, args):
                return STDOUT_SENTINEL + "nope" if args[5] == "get_available_balance" else super().__call__(args)
        _, out, _ = self.run_main(["intents-status"], Blind())
        self.assertIn("available balance now: could not be read", out)

    def test_a_damaged_log_does_not_break_the_status(self):
        self.log.path.write_text("not json\n")
        code, out, _ = self.run_main(["intents-status"], Chain())
        self.assertEqual(code, 0)
        self.assertIn("not shown", out)


class RepeatedSpendWarning(Base):
    def test_a_second_spend_for_the_same_approval_warns_but_is_not_refused(self):
        self.add({"event": "approval_sent", "ts": self.now.isoformat(), "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                  "deposit_address": HEX64, "quote_hash": "ab" * 32, "expires_ns": str(int(self.now.timestamp() * 1e6) * 1000 + 10 * 60 * 10**9)})
        _, first = swap.prepare_spend(self.cfg, self.log, 0, "0.1", record=True, now=self.now)
        self.assertFalse([line for line in first if "WARNING" in line])
        args, second = swap.prepare_spend(self.cfg, self.log, 0, "0.1", record=True, now=self.now)
        warning = [line for line in second if "WARNING" in line]
        self.assertEqual(len(warning), 1)
        for text in ("earlier spend for this approval", self.now.isoformat(), "make intents-status"):
            self.assertIn(text, warning[0])
        self.assertEqual(args[5], "spend_intent")

    def test_a_spend_for_another_approval_does_not_warn(self):
        self.add({"event": "spend_sent", "ts": "x", "agent": self.cfg.agent, "deposit_address": OTHER})
        self.add({"event": "approval_sent", "ts": self.now.isoformat(), "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                  "deposit_address": HEX64, "quote_hash": "ab" * 32, "expires_ns": str(int(self.now.timestamp() * 1e6) * 1000 + 10 * 60 * 10**9)})
        _, extra = swap.prepare_spend(self.cfg, self.log, 0, "0.1", record=False, now=self.now)
        self.assertFalse([line for line in extra if "WARNING" in line])


class FlakyChain(Chain):
    """A fake `near` whose transactions can be refused by the node and whose views can fail or show approvals."""

    def __init__(self, log=None, fail_tx=0, approvals=None, fail_views=(), **kw):
        super().__init__(**kw)
        self.log, self.fail_tx, self.approvals, self.fail_views, self.log_at_send = log, fail_tx, approvals or [], fail_views, []

    def __call__(self, args):
        if "as-read-only" in args:
            if args[5] in self.fail_views:
                raise ChainError("view refused")
            if args[5] == "get_approvals":
                return STDOUT_SENTINEL + json.dumps(self.approvals)
            return super().__call__(args)
        self.log_at_send.append([e.get("event") for e in self.log.entries()] if self.log else [])
        if self.fail_tx > 0:
            self.fail_tx -= 1
            raise ChainError("node refused the call")
        return super().__call__(args)


class SendOrder(Base):
    """The private log is written right before the send, after every refusal, and a failed send is recorded as failed."""

    def approve_cli(self, chain):
        return self.run_main(["intents-approve", "--amount", "0.1", "--yes"], chain)

    def test_the_approval_is_logged_immediately_before_the_send_and_a_refused_send_does_not_block_a_retry(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = FlakyChain(self.log, fail_tx=1)
        code, _, err = self.approve_cli(chain)
        self.assertEqual(code, 1)
        self.assertEqual(chain.log_at_send[0].count("approval_sent"), 1)  # already in the log when the CLI was called
        self.assertEqual(len(self.events("send_failed")), 1)
        code, _, err = self.approve_cli(chain)  # the retry is not refused as "already approved"
        self.assertEqual(code, 0, err)
        self.assertEqual(len(chain.calls), 1)

    def test_a_refusal_before_the_send_leaves_no_trace_in_the_log(self):
        self.add(quote_entry(self.cfg, utc_now()))
        self.add({"event": "approval_sent", "ts": utc_now().isoformat(), "agent": self.cfg.agent, "route_id": 0, "amount": str(NEAR // 10),
                  "deposit_address": HEX64, "quote_hash": "ab" * 32, "expires_ns": str(int(utc_now().timestamp() * 1e6) * 1000 + 10 * 60 * 10**9)})
        chain = FlakyChain(self.log, fail_views=("get_policy",))
        code, _, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 1)
        self.assertIn("nothing was sent", err)
        self.assertEqual((chain.calls, self.events("spend_sent"), self.events("spend_balance_before"), self.events("send_failed")), ([], [], [], []))

    def test_the_chain_is_the_truth_for_an_existing_approval(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = FlakyChain(self.log, approvals=[{"deposit_account": HEX64, "amount": str(NEAR // 10)}])
        code, _, err = self.approve_cli(chain)
        self.assertEqual(code, 1)
        self.assertIn("the chain already holds an approval", err)
        self.assertEqual((chain.calls, self.events("approval_sent")), ([], []))

    def test_unreadable_approvals_on_the_chain_stop_the_approval(self):
        self.add(quote_entry(self.cfg, utc_now()))
        chain = FlakyChain(self.log, fail_views=("get_approvals",))
        code, _, err = self.approve_cli(chain)
        self.assertEqual(code, 1)
        self.assertIn("could not be read, so nothing was approved", err)
        self.assertEqual((chain.calls, self.events("approval_sent")), ([], []))

    def test_a_failed_spend_is_logged_and_the_retry_has_no_false_warning(self):
        self.add(quote_entry(self.cfg, utc_now()))
        self.assertEqual(self.approve_cli(FlakyChain(self.log))[0], 0)
        chain = FlakyChain(self.log, fail_tx=1)
        code, _, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 1)
        self.assertIn("does not prove that the transaction did not land", err)
        self.assertIn("spend_sent", chain.log_at_send[0])
        self.assertIn("spend_balance_before", chain.log_at_send[0])
        self.assertEqual(len(self.events("send_failed")), 1)
        code, out, err = self.run_main(["intents-spend", "--amount", "0.1", "--yes"], chain)
        self.assertEqual(code, 0, err)
        self.assertNotIn("WARNING", out)


if __name__ == "__main__":
    unittest.main()
