import datetime
import unittest
from unittest import mock

from leash_agent import intents
from leash_agent.intents import (
    PLATFORM_FEE_BPS, PLATFORM_FEE_RECIPIENT, IntentsError, OneClick, QuoteRejected, build_quote_request, check_url, find_tokens, iso, verify_quote_response,
)

NOW = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
NEAR = 10**24
ASSET = "nep141:base-0xabc.omft.near"
DEPOSIT = "a" * 64


def make_request(dry=False, amount=NEAR // 2):
    return build_quote_request(dest_asset=ASSET, recipient="0xRecipient", refund_to="v.near", amount=amount,
                               dry=dry, now=NOW)


def good_response(req, **over):
    body = req.body()
    quote = {"amountIn": body["amount"], "amountOut": "2000000", "minAmountOut": "1980000", "timeEstimate": 120}
    if not req.dry:
        quote.update(depositAddress=DEPOSIT, deadline=iso(NOW + datetime.timedelta(minutes=20)))
    response = {"quoteRequest": dict(body), "quote": quote, "signature": "ed25519:sig", "timestamp": iso(NOW)}
    for key, value in over.items():
        if "__" not in key:
            response[key] = value
            continue
        target, _, name = key.partition("__")
        response["quoteRequest" if target == "echo" else "quote"][name] = value
    return response


class BuildRequest(unittest.TestCase):
    def test_the_body_comes_from_the_route_and_constants(self):
        body = make_request().body()
        self.assertEqual(body["originAsset"], "nep141:wrap.near")
        self.assertEqual((body["depositType"], body["refundType"], body["recipientType"]),
                         ("ORIGIN_CHAIN", "ORIGIN_CHAIN", "DESTINATION_CHAIN"))
        self.assertEqual((body["recipient"], body["refundTo"], body["amount"], body["swapType"]),
                         ("0xRecipient", "v.near", str(NEAR // 2), "EXACT_INPUT"))
        self.assertEqual(body["deadline"], "2026-11-01T12:20:00.000Z")
        self.assertEqual(set(body), {"dry", "swapType", "slippageTolerance", "originAsset", "depositType",
                                     "destinationAsset", "amount", "recipient", "recipientType", "refundTo",
                                     "refundType", "deadline"})

    def test_bad_inputs_are_refused(self):
        bad = [dict(amount=0), dict(amount=-1), dict(amount=True), dict(amount="5"), dict(deadline_minutes=31),
               dict(deadline_minutes=0), dict(slippage_bps=0), dict(slippage_bps=301), dict(refund_to="Bad Account"),
               dict(recipient=""), dict(recipient="a\nb"), dict(recipient=" x"), dict(dest_asset="x" * 129)]
        for over in bad:
            kwargs = dict(dest_asset=ASSET, recipient="0xR", refund_to="v.near", amount=1, dry=True, now=NOW)
            kwargs.update(over)
            with self.assertRaises(IntentsError, msg=str(over)):
                build_quote_request(**kwargs)


class VerifyQuote(unittest.TestCase):
    def test_a_matching_real_quote_passes(self):
        req = make_request()
        v = verify_quote_response(req, good_response(req), now=NOW)
        self.assertEqual((v.deposit_address, v.amount_in, v.signature), (DEPOSIT, NEAR // 2, "ed25519:sig"))

    def test_a_matching_dry_quote_passes_and_has_no_address(self):
        req = make_request(dry=True)
        v = verify_quote_response(req, good_response(req), now=NOW)
        self.assertIsNone(v.deposit_address)

    def test_hostile_responses_are_rejected(self):
        req = make_request()
        cases = {
            "other recipient": good_response(req, echo__recipient="0xAttacker"),
            "other refundTo": good_response(req, echo__refundTo="attacker.near"),
            "other origin asset": good_response(req, echo__originAsset="nep141:other.near"),
            "other destination asset": good_response(req, echo__destinationAsset="nep141:eth.omft.near"),
            "other amount echo": good_response(req, echo__amount="1"),
            "other deposit type": good_response(req, echo__depositType="INTENTS"),
            "other slippage": good_response(req, echo__slippageTolerance=5000),
            "changed amountIn": good_response(req, quote__amountIn="1"),
            "custom recipient msg": good_response(req, echo__customRecipientMsg="evil.near"),
            "app fees": good_response(req, echo__appFees=[{"recipient": "x.near", "fee": 100}]),
            "virtual chain recipient": good_response(req, quote__virtualChainRecipient="0x1"),
            "deposit not a NEAR account": good_response(req, quote__depositAddress="0xDEADBEEF"),
            "deposit address empty": good_response(req, quote__depositAddress=""),
            "deposit address is the vault": good_response(req, quote__depositAddress="v.near"),
            "deposit address is the recipient": good_response(req, quote__depositAddress="0xrecipient"),
            "memo needed": good_response(req, quote__depositMemo="123"),
            "deadline in the past": good_response(req, quote__deadline=iso(NOW - datetime.timedelta(minutes=1))),
            "deadline too far": good_response(req, quote__deadline=iso(NOW + datetime.timedelta(days=5))),
            "deadline before the requested one": good_response(req, quote__deadline=iso(NOW + datetime.timedelta(minutes=5))),
            "deadline missing": good_response(req, quote__deadline=None),
            "deadline garbage": good_response(req, quote__deadline="tomorrow"),
            "min out below the floor": good_response(req, quote__minAmountOut="1000"),
            "min out above the amount out": good_response(req, quote__minAmountOut="3000000"),
            "pays nothing": good_response(req, quote__amountOut="0", quote__minAmountOut="0"),
            "amount with a decimal": good_response(req, quote__amountOut="2.5"),
            "no signature": good_response(req, signature=""),
        }
        for name, response in cases.items():
            with self.assertRaises(QuoteRejected, msg=name):
                verify_quote_response(req, response, now=NOW)

    def test_only_the_recorded_platform_fee_is_accepted(self):
        good = [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": PLATFORM_FEE_BPS}]
        for dry in (True, False):
            req = make_request(dry=dry)
            verify_quote_response(req, good_response(req, echo__appFees=good), now=NOW)  # the live shape passes
            verify_quote_response(req, good_response(req, echo__appFees=[]), now=NOW)    # and so does none
        req = make_request()
        bad = {
            "other recipient": [{"recipient": "attacker.near", "fee": 20}],
            "higher fee": [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": 21}],
            "lower fee": [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": 19}],
            "fee as text": [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": "20"}],
            "fee as bool": [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": True}],
            "two entries": good + [{"recipient": "attacker.near", "fee": 1}],
            "same entry twice": good + good,
            "limit order set": [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": 20, "limitOrderId": "abc"}],
            "extra field": [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": 20, "memo": "x"}],
            "not a list": good[0],
            "text": "fee",
            "upper-case recipient": [{"recipient": PLATFORM_FEE_RECIPIENT.upper(), "fee": 20}],
        }
        for name, fees in bad.items():
            with self.assertRaises(QuoteRejected, msg=name):
                verify_quote_response(req, good_response(req, echo__appFees=fees), now=NOW)
        with self.assertRaises(QuoteRejected, msg="quote-level fee, even the pinned one"):
            verify_quote_response(req, good_response(req, quote__appFees=good), now=NOW)

    def test_the_live_wet_shape_is_accepted_and_the_address_life_is_bounded(self):
        req = make_request(dry=False)
        live = [{"recipient": PLATFORM_FEE_RECIPIENT, "fee": PLATFORM_FEE_BPS, "limitOrderId": None}]
        asked = NOW + datetime.timedelta(minutes=20)
        for life in (datetime.timedelta(0), datetime.timedelta(days=3), datetime.timedelta(days=4)):
            answer = good_response(req, echo__appFees=live, quote__deadline=iso(asked + life))
            self.assertEqual(verify_quote_response(req, answer, now=NOW).deposit_address, DEPOSIT)
        answer = good_response(req, quote__deadline=iso(asked + datetime.timedelta(days=4, seconds=1)))
        with self.assertRaises(QuoteRejected):
            verify_quote_response(req, answer, now=NOW)

    def test_structurally_bad_responses_are_rejected(self):
        req = make_request()
        for response in (None, [], {}, {"quote": {}}, {"quote": []}, {"quote": {"amountIn": "1"}, "quoteRequest": None}):
            with self.assertRaises(QuoteRejected):
                verify_quote_response(req, response, now=NOW)

    def test_a_dry_quote_with_an_address_is_rejected(self):
        req = make_request(dry=True)
        with self.assertRaises(QuoteRejected):
            verify_quote_response(req, good_response(req, quote__depositAddress=DEPOSIT), now=NOW)

    def test_forbidden_deposit_accounts_are_rejected(self):
        req = make_request()
        for account in ("agent.near", "guardian.near", "owner.near"):
            with self.assertRaises(QuoteRejected, msg=account):
                verify_quote_response(req, good_response(req, quote__depositAddress=account), now=NOW,
                                      forbidden_deposit=("agent.near", "guardian.near", "owner.near"))


class Client(unittest.TestCase):
    def test_only_the_1click_host_over_https_is_allowed(self):
        self.assertEqual(check_url("https://1click.chaindefuser.com"), "https://1click.chaindefuser.com")
        for url in ("http://1click.chaindefuser.com", "https://evil.example", "https://1click.chaindefuser.com:8443",
                    "https://user:pw@1click.chaindefuser.com", "https://1click.chaindefuser.com.evil.example"):
            with self.assertRaises(IntentsError, msg=url):
                check_url(url)
        with mock.patch.dict("os.environ", {"LEASH_ALLOW_CUSTOM_1CLICK_HOST": "1"}):
            self.assertEqual(check_url("https://example.org"), "https://example.org")

    def test_calls_and_headers(self):
        seen = []

        def http(method, url, headers, body):
            seen.append((method, url, headers, body))
            if url.endswith("/v0/tokens"):
                return [{"assetId": "nep141:wrap.near", "symbol": "wNEAR", "blockchain": "near"}, "junk", {"x": 1}]
            return {"status": "PROCESSING"}

        api = OneClick(api_key="secret-key", http=http)
        self.assertEqual(len(api.tokens()), 1)
        self.assertEqual(api.status(DEPOSIT)["status"], "PROCESSING")
        self.assertEqual(seen[0][0:2], ("GET", "https://1click.chaindefuser.com/v0/tokens"))
        self.assertEqual(seen[0][2]["X-API-Key"], "secret-key")
        self.assertTrue(seen[1][1].endswith(f"/v0/status?depositAddress={DEPOSIT}"))
        self.assertNotIn("X-API-Key", OneClick(http=http)._headers())
        self.assertEqual(seen[0][2]["User-Agent"], intents.USER_AGENT)
        self.assertNotIn("Python-urllib", intents.USER_AGENT)

    def test_status_rejects_bad_input_and_answers(self):
        api = OneClick(http=lambda *a: {"status": "WEIRD"})
        with self.assertRaises(IntentsError):
            api.status(DEPOSIT)
        with self.assertRaises(IntentsError):
            api.status("0x123&x=1")
        with self.assertRaises(IntentsError):
            OneClick(http=lambda *a: {}).tokens()

    def test_no_redirects_and_size_cap(self):
        import urllib.error
        err = urllib.error.HTTPError("https://1click.chaindefuser.com", 302, "Found", {}, None)
        with mock.patch.object(intents._OPENER, "open", side_effect=err):
            with self.assertRaisesRegex(IntentsError, "redirect"):
                intents.http_request("GET", "https://1click.chaindefuser.com/v0/tokens", {}, None)

        class Big:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b"x" * n
        with mock.patch.object(intents._OPENER, "open", return_value=Big()):
            with self.assertRaisesRegex(IntentsError, "too large"):
                intents.http_request("GET", "https://1click.chaindefuser.com/v0/tokens", {}, None)

    def test_find_tokens(self):
        tokens = [{"assetId": "a", "symbol": "USDC", "blockchain": "base"}, {"assetId": "b", "symbol": "usdc", "blockchain": "eth"},
                  {"assetId": "c", "symbol": "ETH", "blockchain": "base"}]
        self.assertEqual([t["assetId"] for t in find_tokens(tokens, "USDC", "base")], ["a"])
        self.assertEqual(len(find_tokens(tokens, "usdc")), 2)
        self.assertEqual(len(find_tokens(tokens)), 3)


if __name__ == "__main__":
    unittest.main()
