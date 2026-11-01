import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leash_agent import __main__ as cli
from leash_agent.chain import Snapshot
from leash_agent.guardian import EnvRoutes, Guardian, GuardianLog, Refused, intent_denial
from leash_agent.intents import IntentsError, QuoteRejected, parse_iso
from tests.test_chain import make_config
from tests.test_intents import ASSET, DEPOSIT, NOW

NEAR = 10**24


def snap(**over):
    base = dict(per_tx_cap=NEAR, daily_cap=3 * NEAR, spent=0, allowlist=("p.testnet",),
                agent_paused=False, all_paused=False, balance=5 * NEAR)
    base.update(over)
    return Snapshot(**base)


class FakeOneClick:
    """Answers a quote by echoing the request, like the real service; `tamper` edits the answer."""

    def __init__(self, tamper=None, statuses=("PENDING_DEPOSIT", "PROCESSING", "SUCCESS")):
        self.tamper, self.quotes, self.statuses = tamper, [], list(statuses)

    def quote(self, req):
        self.quotes.append(req)
        body = req.body()
        quote = {"amountIn": body["amount"], "amountOut": "2000000", "minAmountOut": "1980000", "timeEstimate": 90}
        if not req.dry:
            quote.update(depositAddress=DEPOSIT, deadline=body["deadline"])
        response = {"quoteRequest": body, "quote": quote, "signature": "ed25519:sig"}
        if self.tamper:
            self.tamper(response)
        return response

    def status(self, address):
        return {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]}


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = GuardianLog(Path(self.tmp.name) / "g.jsonl")
        self.clock = Clock()
        self.snapshot = snap()

    def make(self, oneclick=None, **kw):
        self.oneclick = oneclick or FakeOneClick()
        return Guardian(agent="a.near", vault_account="v.near", snapshot=lambda: self.snapshot,
                        routes=EnvRoutes(ASSET, "0xRecipient"), oneclick=self.oneclick, log=self.log,
                        other_accounts=("owner.near", "guardian.near"), now=self.clock, sleep=lambda s: None, **kw)

    def test_a_good_quote_is_built_only_from_the_route(self):
        issued = self.make().quote(0, NEAR // 2)
        body = self.oneclick.quotes[0].body()
        self.assertEqual((body["recipient"], body["refundTo"], body["destinationAsset"], body["dry"]),
                         ("0xRecipient", "v.near", ASSET, False))
        self.assertEqual(issued.verified.deposit_address, DEPOSIT)
        entry = self.log.entries()[-1]
        self.assertEqual((entry["event"], entry["signature"], entry["deposit_address"]), ("quote_issued", "ed25519:sig", DEPOSIT))
        self.assertEqual(oct(self.log.path.stat().st_mode & 0o777), "0o600")

    def test_the_agent_cannot_choose_anything_but_route_and_amount(self):
        guardian = self.make()
        with self.assertRaises(TypeError):
            guardian.quote(0, NEAR, recipient="0xAttacker")  # type: ignore[call-arg]
        for route, amount in ((10, 1), (-1, 1), (True, 1), ("0", 1), (1, 1), (0, 0), (0, -5), (0, "5"), (0, True), (0, 1.5)):
            with self.assertRaises(Refused, msg=(route, amount)):
                guardian.quote(route, amount)  # type: ignore[arg-type]
        self.assertEqual(self.oneclick.quotes, [])

    def test_it_refuses_early_when_the_vault_would_deny(self):
        cases = [
            (dict(all_paused=True), NEAR, "all_paused"), (dict(agent_paused=True), NEAR, "agent_paused"),
            (dict(), NEAR + 1, "over_per_tx_cap"), (dict(spent=5 * NEAR // 2), NEAR // 2 + 1, "over_daily_cap"),
            (dict(balance=NEAR // 10), NEAR // 2, "insufficient_funds"),
        ]
        for over, amount, reason in cases:
            self.snapshot = snap(**over)
            guardian = self.make()
            with self.assertRaisesRegex(Refused, reason):
                guardian.quote(0, amount)
            self.assertEqual(guardian.oneclick.quotes, [], reason)
        self.assertTrue(all(e["event"] == "refused" for e in self.log.entries()))

    def test_denial_order_matches_the_contract(self):
        self.assertEqual(intent_denial(snap(all_paused=True, agent_paused=True), 10 * NEAR), "all_paused")
        self.assertEqual(intent_denial(snap(agent_paused=True), 10 * NEAR), "agent_paused")
        self.assertEqual(intent_denial(snap(), 10 * NEAR), "over_per_tx_cap")
        self.assertEqual(intent_denial(snap(spent=3 * NEAR), NEAR), "over_daily_cap")
        self.assertEqual(intent_denial(snap(balance=0), NEAR), "insufficient_funds")
        self.assertIsNone(intent_denial(snap(), NEAR))

    def test_each_hostile_response_is_refused_and_logged_without_an_issued_quote(self):
        def set_echo(key, value):
            return lambda r: r["quoteRequest"].__setitem__(key, value)

        tampers = {
            "recipient": set_echo("recipient", "0xAttacker"), "refundTo": set_echo("refundTo", "attacker.near"),
            "asset": set_echo("destinationAsset", "nep141:eth.omft.near"),
            "amount": lambda r: r["quote"].__setitem__("amountIn", "1"),
            "deposit not NEAR": lambda r: r["quote"].__setitem__("depositAddress", "0xDEAD"),
            "deposit is a LEASH account": lambda r: r["quote"].__setitem__("depositAddress", "guardian.near"),
        }
        for name, tamper in tampers.items():
            guardian = self.make(FakeOneClick(tamper))
            with self.assertRaises(QuoteRejected, msg=name):
                guardian.quote(0, NEAR // 2)
        events = [e["event"] for e in self.log.entries() if e["event"] != "quote_call"]
        self.assertEqual(events, ["quote_rejected"] * len(tampers))

    def test_rate_limit_per_hour(self):
        guardian = self.make(max_per_hour=2)
        for _ in range(2):
            guardian.quote(0, NEAR // 2)
            guardian.watch(DEPOSIT)  # finished, so only the hourly limit applies
            self.clock.now += datetime.timedelta(minutes=5)
        with self.assertRaisesRegex(Refused, "rate_limit"):
            guardian.quote(0, NEAR // 2)
        self.clock.now += datetime.timedelta(hours=1)
        guardian.quote(0, NEAR // 2)

    def test_one_outstanding_quote_at_a_time(self):
        guardian = self.make()
        guardian.quote(0, NEAR // 2)
        with self.assertRaisesRegex(Refused, "outstanding_quote"):
            guardian.quote(0, NEAR // 2)
        self.clock.now += datetime.timedelta(minutes=21)  # past the deadline
        guardian.quote(0, NEAR // 2)

    def test_a_finished_quote_frees_the_slot(self):
        guardian = self.make()
        guardian.quote(0, NEAR // 2)
        guardian.watch(DEPOSIT)
        guardian.quote(0, NEAR // 2)

    def test_watch_records_the_final_status_and_gives_up(self):
        guardian = self.make(FakeOneClick(statuses=("PENDING_DEPOSIT", "REFUNDED")))
        self.assertEqual(guardian.watch(DEPOSIT)["status"], "REFUNDED")
        self.assertEqual(self.log.entries()[-1]["status"], "REFUNDED")
        stuck = self.make(FakeOneClick(statuses=("PROCESSING",)))
        with self.assertRaisesRegex(IntentsError, "no final status"):
            stuck.watch(DEPOSIT, max_wait_s=30, interval_s=15)

    def test_a_damaged_log_stops_real_quotes(self):
        self.log.path.write_text("{not json\n")
        with self.assertRaisesRegex(IntentsError, "damaged"):
            self.make().quote(0, NEAR // 2)

    def test_dry_quote_reads_no_vault_state_and_writes_no_issued_quote(self):
        guardian = Guardian(agent="a.near", vault_account="v.near", snapshot=mock.Mock(side_effect=AssertionError("vault read")),
                            routes=EnvRoutes(ASSET, "0xRecipient"), oneclick=FakeOneClick(), log=self.log, now=self.clock)
        issued = guardian.dry_quote(0, NEAR // 2, refund_to="real.near")
        self.assertTrue(issued.request.dry)
        self.assertEqual(issued.request.refund_to, "real.near")
        self.assertEqual([e["event"] for e in self.log.entries()], ["quote_call"])  # counted, nothing issued

    def test_env_routes(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(IntentsError):
                EnvRoutes.from_env()
        with mock.patch.dict("os.environ", {"LEASH_ROUTE_RECIPIENT": "0xR", "LEASH_ROUTE_DEST_ASSET": "x"}, clear=True):
            self.assertEqual(EnvRoutes.from_env().get("a", 0).recipient, "0xR")
            with self.assertRaises(Refused):
                EnvRoutes.from_env().get("a", 1)

    def test_rejected_and_failed_calls_count_toward_the_call_limits(self):
        guardian = self.make(FakeOneClick(lambda r: r["quote"].update(amountIn="1")))
        for _ in range(10):
            with self.assertRaises(QuoteRejected):
                guardian.dry_quote(0, NEAR // 2)
        with self.assertRaises(Refused) as ctx:
            guardian.dry_quote(0, NEAR // 2)
        self.assertIn("rate_limit", str(ctx.exception))
        self.clock.now += datetime.timedelta(seconds=61)  # the minute window moves on
        with self.assertRaises(QuoteRejected):
            guardian.dry_quote(0, NEAR // 2)

    def test_failed_http_calls_are_counted_and_logged(self):
        class Down:
            def quote(self, req):
                raise IntentsError("cannot reach 1Click: down")
        guardian = self.make(Down())
        for _ in range(10):
            with self.assertRaises(IntentsError):
                guardian.dry_quote(0, NEAR // 2)
        self.assertEqual(sum(e["event"] == "quote_failed" for e in self.log.entries()), 10)
        with self.assertRaises(Refused):
            guardian.dry_quote(0, NEAR // 2)

    def test_two_guardians_on_one_log_cannot_both_get_the_only_real_quote(self):
        import threading
        import time

        class Slow(FakeOneClick):
            def quote(self, req):
                time.sleep(0.4)
                return super().quote(req)

        results = []

        def run(guardian):
            try:
                guardian.quote(0, NEAR // 2)
                results.append("issued")
            except Refused as exc:
                results.append(str(exc).split(":")[0])

        first = self.make(Slow())
        second = Guardian(agent="a.near", vault_account="v.near", snapshot=lambda: self.snapshot,
                          routes=EnvRoutes(ASSET, "0xRecipient"), oneclick=Slow(), log=GuardianLog(self.log.path),
                          other_accounts=("owner.near", "guardian.near"), now=self.clock, sleep=lambda s: None)
        threads = [threading.Thread(target=run, args=(first,)), threading.Thread(target=run, args=(second,))]
        threads[0].start()
        time.sleep(0.1)
        threads[1].start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["issued", "outstanding_quote"])

    def test_the_open_quote_rule_follows_the_requested_deadline_not_the_address_life(self):
        def longer(response):
            asked = parse_iso(response["quoteRequest"]["deadline"]) + datetime.timedelta(days=3)
            response["quote"]["deadline"] = asked.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        guardian = self.make(FakeOneClick(longer))
        guardian.quote(0, NEAR // 2)
        with self.assertRaises(Refused):
            guardian.quote(0, NEAR // 2)
        self.clock.now += datetime.timedelta(minutes=21)
        guardian.quote(0, NEAR // 2)

    def test_an_existing_wide_log_file_is_tightened(self):
        self.log.path.write_text("")
        os.chmod(self.log.path, 0o644)
        self.log.append({"event": "x"})
        self.assertEqual(oct(self.log.path.stat().st_mode & 0o777), "0o600")


class Cli(unittest.TestCase):
    class Fake:
        def tokens(self):
            return [{"assetId": ASSET, "symbol": "USDC", "blockchain": "base", "decimals": 6, "price": 1.0},
                    {"assetId": "nep141:wrap.near", "symbol": "wNEAR", "blockchain": "near", "decimals": 24, "price": 4.4}]

        def quote(self, req):
            return FakeOneClick().quote(req)

    def run_main(self, argv, env, fake=None):
        import contextlib
        import io
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out):
            code = cli.main(argv, cfg=make_config(), vault=mock.Mock(), oneclick=fake or self.Fake())
        return code, out.getvalue()

    def test_tokens_lists_and_filters(self):
        code, out = self.run_main(["intents-tokens", "--symbol", "usdc"], {})
        self.assertEqual(code, 0)
        self.assertIn(ASSET, out)
        self.assertNotIn("wNEAR", out)
        self.assertEqual(self.run_main(["intents-tokens", "--symbol", "NOPE"], {})[0], 1)

    def test_dry_prints_the_request_and_the_raw_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LEASH_ROUTE_RECIPIENT": "0xRecipient", "LEASH_GUARDIAN_LOG": f"{tmp}/g.jsonl"}
            code, out = self.run_main(["intents-dry", "--amount", "0.5"], env)
        self.assertEqual(code, 0)
        self.assertIn('"dry": true', out)
        self.assertIn("raw response", out)
        self.assertIn(ASSET, out)  # chosen from the live list: USDC on base
        self.assertNotIn("0xRecipient", out)  # the recipient never reaches the screen
        self.assertIn("<RECIPIENT>", out)

    def test_a_rejected_dry_answer_is_shown_with_the_recipient_hidden_and_logged_in_full(self):
        class AppFees(self.Fake):
            def quote(self, req):
                response = FakeOneClick().quote(req)
                response["quoteRequest"]["appFees"] = [{"recipient": "fees.near", "fee": 20}]
                return response
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LEASH_ROUTE_RECIPIENT": "0xRecipient", "LEASH_GUARDIAN_LOG": f"{tmp}/g.jsonl"}
            code, out = self.run_main(["intents-dry", "--amount", "0.5"], env, AppFees())
            logged = GuardianLog(Path(tmp) / "g.jsonl").entries()
        self.assertEqual(code, 1)
        self.assertIn("REJECTED by the guardian", out)
        self.assertIn("appFees", out)
        self.assertIn("fees.near", out)
        self.assertNotIn("0xRecipient", out)
        self.assertIn("<RECIPIENT>", out)
        self.assertEqual(logged[-1]["event"], "quote_rejected")
        self.assertIn("fees.near", str(logged[-1]["response"]))

    def test_dry_needs_a_recipient_and_exactly_one_usdc_on_base(self):
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, _ = self.run_main(["intents-dry", "--amount", "0.5"], {})
        self.assertEqual(code, 1)
        self.assertIn("LEASH_ROUTE_RECIPIENT", err.getvalue())

        class TwoUsdc(self.Fake):
            def tokens(self):
                return super().tokens() + [{"assetId": "other", "symbol": "USDC", "blockchain": "base"}]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, _ = self.run_main(["intents-dry", "--amount", "0.5"], {"LEASH_ROUTE_RECIPIENT": "0xR"}, TwoUsdc())
        self.assertEqual(code, 1)
        self.assertIn("exactly one USDC", err.getvalue())


class ConfigSuffix(unittest.TestCase):
    def test_mainnet_accounts_end_in_near(self):
        from leash_agent.config import Config
        with mock.patch.dict("os.environ", {"NETWORK": "mainnet", "LEASH_PREFIX": "leashx"}, clear=True), \
                mock.patch("leash_agent.config.load_env_file"):
            cfg = Config.from_env()
        self.assertEqual((cfg.vault, cfg.agent, cfg.network), ("leashx-vault.near", "leashx-agent.near", "mainnet"))
        with mock.patch.dict("os.environ", {"LEASH_PREFIX": "leashx"}, clear=True), mock.patch("leash_agent.config.load_env_file"):
            self.assertEqual(Config.from_env().vault, "leashx-vault.testnet")


if __name__ == "__main__":
    unittest.main()
