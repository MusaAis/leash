"""1Click client: tokens, quote request, quote check, status. Stdlib only, HTTP injectable."""

from __future__ import annotations

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .chain import clean_text, is_valid_account_id

API_HOST = "1click.chaindefuser.com"  # source: docs.near-intents.org, 1Click API reference (servers)
BASE_URL = f"https://{API_HOST}"
WNEAR_ASSET = "nep141:wrap.near"      # the origin asset: the vault wraps NEAR into wNEAR
MAX_RESPONSE_BYTES = 2_000_000        # the token list is the largest answer
MAX_DEADLINE_MINUTES = 30             # spec: the deadline is at most 30 minutes ahead
DEFAULT_DEADLINE_MINUTES = 20
DEFAULT_SLIPPAGE_BPS = 100            # 1%
MAX_SLIPPAGE_BPS = 300
MAX_FIELD_BYTES = 128
USER_AGENT = "leash-agent/0.3 (+https://github.com/MusaAis/leash)"
STATUSES = {"KNOWN_DEPOSIT_TX", "PENDING_DEPOSIT", "INCOMPLETE_DEPOSIT", "PROCESSING", "SUCCESS", "REFUNDED", "FAILED"}
TERMINAL = {"SUCCESS", "REFUNDED", "FAILED"}
# The only fee entry accepted: 1Click's own 0.2%. Recorded from a live answer, see docs/intents.md.
PLATFORM_FEE_RECIPIENT = "5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd"
PLATFORM_FEE_BPS = 20
MAX_ADDRESS_LIFE_DAYS = 4  # the live answer keeps the deposit address 3 days past the requested deadline
# Request fields nobody may set.
FORBIDDEN_ECHO = ("customRecipientMsg", "virtualChainRecipient", "virtualChainRefundRecipient", "appFees", "rebates")

Http = Callable[[str, str, dict, "dict | None"], object]  # (method, url, headers, json body) -> parsed JSON


class IntentsError(RuntimeError):
    """The 1Click call failed or returned something unusable."""


class QuoteRejected(IntentsError):
    """The 1Click response does not match the request, so it must not be approved or paid."""

    response: object = None  # raw answer, for the operator


def check_url(url: str) -> str:
    """Only https on the 1Click host, no credentials, default port: the API key is sent there."""
    parsed = urllib.parse.urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        raise IntentsError(f"1Click URL must be a plain https URL, got {url!r}")
    if host != API_HOST and os.environ.get("LEASH_ALLOW_CUSTOM_1CLICK_HOST") != "1":
        raise IntentsError(
            f"1Click host {host!r} is not allowed (allowed: {API_HOST}). "
            "Set LEASH_ALLOW_CUSTOM_1CLICK_HOST=1 only if intended."
        )
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[override]
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_request(method: str, url: str, headers: dict, body: dict | None) -> object:
    """One JSON request. No redirects, a size cap, a timeout. Error bodies are not echoed in full."""
    request = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method
    )
    try:
        with _OPENER.open(request, timeout=60) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise IntentsError(f"1Click answered HTTP {exc.code} (redirect); refused") from exc
        detail = ""
        try:
            detail = clean_text(exc.read(3000).decode("utf-8", "replace"), 600)
        except Exception:  # noqa: BLE001 - the detail is only a hint
            pass
        raise IntentsError(f"1Click returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise IntentsError(f"cannot reach 1Click: {exc.reason}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise IntentsError("1Click response is too large; refused")
    try:
        return json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise IntentsError("1Click response is not JSON") from exc


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_iso(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise ValueError("not a string")
    moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("no time zone")
    return moment


@dataclass(frozen=True)
class QuoteRequest:
    """What the guardian asks 1Click for. Built only by `build_quote_request`."""

    dry: bool
    origin_asset: str
    destination_asset: str
    recipient: str
    refund_to: str
    amount: int
    slippage_bps: int
    deadline: datetime.datetime

    def body(self) -> dict:
        return {
            "dry": self.dry,
            "swapType": "EXACT_INPUT",
            "slippageTolerance": self.slippage_bps,
            "originAsset": self.origin_asset,
            "depositType": "ORIGIN_CHAIN",
            "destinationAsset": self.destination_asset,
            "amount": str(self.amount),
            "recipient": self.recipient,
            "recipientType": "DESTINATION_CHAIN",
            "refundTo": self.refund_to,
            "refundType": "ORIGIN_CHAIN",
            "deadline": iso(self.deadline),
        }


def _field(name: str, value: object) -> str:
    if (
        not isinstance(value, str) or not value or len(value.encode()) > MAX_FIELD_BYTES
        or clean_text(value) != value or value != value.strip()
    ):
        raise IntentsError(f"{name} must be a non-empty plain string of at most {MAX_FIELD_BYTES} bytes")
    return value


def build_quote_request(*, dest_asset: str, recipient: str, refund_to: str, amount: int, dry: bool,
                        now: datetime.datetime | None = None, deadline_minutes: int = DEFAULT_DEADLINE_MINUTES,
                        slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> QuoteRequest:
    """Build the request from the owner's route and constants. The agent supplies only an amount."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise IntentsError("amount must be a positive integer (yoctoNEAR)")
    if not 1 <= deadline_minutes <= MAX_DEADLINE_MINUTES:
        raise IntentsError(f"deadline must be 1 to {MAX_DEADLINE_MINUTES} minutes")
    if not 1 <= slippage_bps <= MAX_SLIPPAGE_BPS:
        raise IntentsError(f"slippage must be 1 to {MAX_SLIPPAGE_BPS} basis points")
    if not is_valid_account_id(refund_to):
        raise IntentsError("refund_to must be a valid NEAR account id (the vault)")
    return QuoteRequest(
        dry=dry, origin_asset=WNEAR_ASSET, destination_asset=_field("dest_asset", dest_asset),
        recipient=_field("recipient", recipient), refund_to=refund_to, amount=amount,
        slippage_bps=slippage_bps, deadline=(now or utc_now()) + datetime.timedelta(minutes=deadline_minutes),
    )


@dataclass(frozen=True)
class VerifiedQuote:
    dry: bool
    amount_in: int
    amount_out: int
    min_amount_out: int
    deposit_address: str | None
    deadline: datetime.datetime | None
    signature: str | None
    time_estimate: int | None


def _int(value: object, name: str) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise QuoteRejected(f"response {name} is not a plain integer string: {value!r}")
    return int(value)


def verify_quote_response(req: QuoteRequest, response: object, *, now: datetime.datetime | None = None,
                          forbidden_deposit: tuple[str, ...] = ()) -> VerifiedQuote:
    """Check the answer against the request. Anything unexpected raises QuoteRejected (fail closed)."""
    now = now or utc_now()
    if not isinstance(response, dict) or not isinstance(response.get("quote"), dict):
        raise QuoteRejected("response has no quote object")
    echo, quote = response.get("quoteRequest"), response["quote"]
    if not isinstance(echo, dict):
        raise QuoteRejected("response does not echo the quote request, so it cannot be checked")
    body = req.body()
    for key in ("dry", "swapType", "slippageTolerance", "originAsset", "destinationAsset", "recipient", "refundTo", "depositType",
                "refundType", "recipientType", "amount"):
        if echo.get(key) != body[key]:
            detail = "" if key in ("recipient", "refundTo") else f": sent {body[key]!r}, got {echo.get(key)!r}"
            raise QuoteRejected(f"response changed {key}{detail}")
    fees = echo.get("appFees")
    if fees not in (None, "", [], {}):
        entry = fees[0] if isinstance(fees, list) and len(fees) == 1 else None
        if not (isinstance(entry, dict) and set(entry) - {"limitOrderId"} == {"recipient", "fee"}
                and entry.get("limitOrderId") is None
                and entry["recipient"] == PLATFORM_FEE_RECIPIENT
                and isinstance(entry["fee"], int) and not isinstance(entry["fee"], bool)
                and entry["fee"] == PLATFORM_FEE_BPS):
            raise QuoteRejected("response carries appFees other than the recorded 1Click platform fee")
    for key in FORBIDDEN_ECHO:
        if key != "appFees" and echo.get(key) not in (None, "", [], {}):
            raise QuoteRejected(f"response carries {key}, which LEASH never sets")
        if quote.get(key) not in (None, "", [], {}):
            raise QuoteRejected(f"quote carries {key}, which LEASH never sets")
    amount_in = _int(quote.get("amountIn"), "amountIn")
    if amount_in != req.amount:
        raise QuoteRejected(f"amountIn {amount_in} is not the requested amount {req.amount}")
    amount_out, min_out = _int(quote.get("amountOut"), "amountOut"), _int(quote.get("minAmountOut"), "minAmountOut")
    if amount_out <= 0 or min_out <= 0:
        raise QuoteRejected("the quote pays out nothing")
    floor = amount_out * (10_000 - req.slippage_bps) // 10_000
    if min_out < floor - 1 or min_out > amount_out:  # one unit of rounding is allowed
        raise QuoteRejected(f"minAmountOut {min_out} is outside the slippage limit (floor {floor}, amountOut {amount_out})")
    address, deadline, signature = quote.get("depositAddress"), None, response.get("signature")
    if quote.get("depositMemo") not in (None, ""):
        raise QuoteRejected("the quote needs a deposit memo; the vault cannot send one")
    if req.dry:
        if address not in (None, ""):
            raise QuoteRejected("a dry quote must not contain a deposit address")
    else:
        if not is_valid_account_id(address):
            raise QuoteRejected(f"deposit address is not a valid NEAR account id: {address!r}")
        if address.lower() in {a.lower() for a in (req.refund_to, req.recipient, *forbidden_deposit)}:
            raise QuoteRejected("deposit address equals the vault, the recipient or another LEASH account")
        try:
            deadline = parse_iso(quote.get("deadline"))
        except (ValueError, TypeError) as exc:
            raise QuoteRejected(f"quote deadline is missing or invalid: {exc}") from exc
        asked = parse_iso(body["deadline"])
        if not (now < deadline and asked <= deadline <= asked + datetime.timedelta(days=MAX_ADDRESS_LIFE_DAYS)):
            raise QuoteRejected(f"quote deadline {deadline.isoformat()} is outside the allowed range")
        if not isinstance(signature, str) or not signature:
            raise QuoteRejected("the quote has no signature to keep")
    estimate = quote.get("timeEstimate")
    return VerifiedQuote(
        dry=req.dry, amount_in=amount_in, amount_out=amount_out, min_amount_out=min_out,
        deposit_address=address or None, deadline=deadline, signature=signature if not req.dry else None,
        time_estimate=int(estimate) if isinstance(estimate, (int, float)) and not isinstance(estimate, bool) else None,
    )


class OneClick:
    """The three calls LEASH needs. The API key (optional) is sent only to the allowed host."""

    def __init__(self, api_key: str | None = None, http: Http = http_request, base_url: str = BASE_URL) -> None:
        self.api_key, self._http, self.base_url = api_key or None, http, check_url(base_url).rstrip("/")

    @classmethod
    def from_env(cls) -> "OneClick":
        return cls(api_key=os.environ.get("LEASH_1CLICK_API_KEY"))

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["X-API-Key"] = self.api_key  # docs: X-API-Key is the recommended header
        return headers

    def tokens(self) -> list[dict]:
        data = self._http("GET", f"{self.base_url}/v0/tokens", self._headers(), None)
        if not isinstance(data, list):
            raise IntentsError("token list is not a list")
        return [t for t in data if isinstance(t, dict) and isinstance(t.get("assetId"), str)]

    def quote(self, req: QuoteRequest) -> object:
        return self._http("POST", f"{self.base_url}/v0/quote", self._headers(), req.body())

    def status(self, deposit_address: str) -> dict:
        if not is_valid_account_id(deposit_address):
            raise IntentsError("deposit address is not a valid NEAR account id")
        query = urllib.parse.urlencode({"depositAddress": deposit_address})
        data = self._http("GET", f"{self.base_url}/v0/status?{query}", self._headers(), None)
        if not isinstance(data, dict) or data.get("status") not in STATUSES:
            raise IntentsError(f"unexpected status answer (fields: {sorted(data)[:10] if isinstance(data, dict) else type(data).__name__})")
        return data


def find_tokens(tokens: list[dict], symbol: str | None = None, blockchain: str | None = None) -> list[dict]:
    """Filter the live token list. Asset ids always come from this list, never typed by hand."""
    return [
        t for t in tokens
        if (symbol is None or str(t.get("symbol", "")).upper() == symbol.upper())
        and (blockchain is None or str(t.get("blockchain", "")).lower() == blockchain.lower())
    ]
