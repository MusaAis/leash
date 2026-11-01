"""Wet-quote probe: one real quote to read the deposit address. Never deposit into it."""

from __future__ import annotations

import datetime
import re

from .guardian import GuardianLog
from .intents import (IntentsError, OneClick, QuoteRejected, VerifiedQuote, build_quote_request, iso, utc_now,
                      verify_quote_response)

MAX_PROBE_YOCTO = 15 * 10**22  # 0.15 NEAR


def address_kind(address: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", address):
        return "implicit (64 hex characters)"
    return "named account" if "." in address else "other"


def wet_probe(oneclick: OneClick, log: GuardianLog, *, dest_asset: str, recipient: str, refund_to: str, amount: int,
              forbidden: tuple[str, ...] = (), now: datetime.datetime | None = None) -> tuple[dict, VerifiedQuote, dict]:
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= MAX_PROBE_YOCTO:
        raise IntentsError("the probe amount must be more than 0 and at most 0.15 NEAR")
    req = build_quote_request(dest_asset=dest_asset, recipient=recipient, refund_to=refund_to, amount=amount,
                              dry=False, now=now)
    response = oneclick.quote(req)
    try:
        verified = verify_quote_response(req, response, now=now, forbidden_deposit=forbidden)
    except QuoteRejected as exc:
        log.append({"event": "probe_rejected", "at": iso(now or utc_now()), "reason": str(exc), "response": response})
        exc.response = response
        raise
    log.append({"event": "probe_quote", "at": iso(now or utc_now()), "request": req.body(), "response": response})
    return req.body(), verified, response
