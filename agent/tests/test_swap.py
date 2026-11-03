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
from leash_agent.chain import STDOUT_SENTINEL, Snapshot
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
        self.snapshot_value, self.reads = snapshot or snap(), 0

    def snapshot(self):
        self.reads += 1
        return self.snapshot_value


class Chain:
    """A fake `near`: views answer from the fields, transactions are recorded in `calls`."""

    def __init__(self, balance=5 * NEAR, paused=False, tx_output="ok"):
        self.balance, self.paused, self.tx_output, self.calls, self.views = balance, paused, tx_output, [], []

    def __call__(self, args):
        if "as-read-only" in args:
            self.views.append(args[5])
            answer = {
                "get_policy": {"per_tx_cap": str(NEAR // 2), "daily_cap": str(NEAR), "spent_in_window": "0", "allowlist": [], "paused": False},
                "is_paused_all": self.paused, "get_available_balance": str(self.balance),
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


if __name__ == "__main__":
    unittest.main()
