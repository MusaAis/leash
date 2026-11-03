"""Approval and spend outside testnet: the deposit address and the quote hash come only from the guardian's saved quote."""

from __future__ import annotations

import datetime
import hashlib
import json
import re

from . import callpath
from .callpath import CallPathError
from .chain import Vault, near_to_yocto
from .config import Config
from .guardian import DEFAULT_MAX_PER_HOUR, GuardianLog, intent_denial
from .intents import iso, parse_iso, utc_now

HEX64 = re.compile(r"[0-9a-f]{64}")
QUOTE_MAX_AGE = datetime.timedelta(minutes=30)  # the approval window: an older quote is never approved


def quote_hash(response: dict) -> str:
    """sha256 of the 1Click answer with sorted keys and compact separators; recomputable from the private log."""
    return hashlib.sha256(json.dumps(response, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _when(value: object, what: str) -> datetime.datetime:
    try:
        return parse_iso(value)
    except (ValueError, TypeError) as exc:
        raise CallPathError(f"the guardian log has an entry with no valid {what}; fix or move it") from exc


def _ns(moment: datetime.datetime) -> int:
    return int(moment.timestamp() * 1_000_000) * 1000


def prepare_approval(
    cfg: Config, vault: Vault, log: GuardianLog, route_id: int, amount_near: str, *, record: bool,
    max_minutes: int | None = None, now: datetime.datetime | None = None, max_per_hour: int = DEFAULT_MAX_PER_HOUR,
) -> tuple[list[str], list[str]]:
    """Build `approve_intent` from the newest saved quote, after the checks. Records the approval only if `record`."""
    now = now or utc_now()
    amount = int(near_to_yocto(amount_near))
    with log.lock():  # the limit check and the record are one step
        entries = log.entries()
        quotes = [e for e in entries if e.get("event") == "quote_issued" and e.get("agent") == cfg.agent and e.get("route_id") == route_id]
        if not quotes:
            raise CallPathError("no saved quote for this route in the guardian log: run make intents-quote first")
        quote = quotes[-1]
        if str(quote.get("amount")) != str(amount):
            raise CallPathError(f"the newest saved quote is for {callpath.yocto_to_near(quote.get('amount', 0))} NEAR, not {amount_near} NEAR: ask for a new quote")
        quoted_at = _when(quote.get("ts"), "time")
        if now - quoted_at > QUOTE_MAX_AGE:
            raise CallPathError(f"the saved quote is older than 30 minutes (saved {iso(quoted_at)}): ask for a new quote")
        deadline = _when(quote.get("request_deadline"), "quote deadline")
        deposit, response = quote.get("deposit_address"), quote.get("response")
        if not isinstance(deposit, str) or not HEX64.fullmatch(deposit) or not isinstance(response, dict):
            raise CallPathError("the saved quote has no deposit address of the expected shape (64 lowercase hex characters)")
        minutes = min(callpath.MAX_APPROVAL_MINUTES, max_minutes or callpath.MAX_APPROVAL_MINUTES, int((deadline - now).total_seconds() // 60))
        if minutes < 1:
            raise CallPathError(f"the saved quote's deadline ({iso(deadline)}) has passed or is less than a minute away: ask for a new quote")
        if any(e.get("event") == "approval_sent" and e.get("deposit_address") == deposit for e in entries):
            raise CallPathError("this quote was already approved; an approval is single use: ask for a new quote")
        recent = 0
        for entry in entries:
            if entry.get("event") == "approval_sent" and now - _when(entry.get("ts"), "time") < datetime.timedelta(hours=1):
                recent += 1
        if recent >= max_per_hour:
            raise CallPathError(f"rate_limit: {recent} approvals in the last hour (limit {max_per_hour})")
        reason = intent_denial(vault.snapshot(), amount)  # fresh vault state, same order as the contract
        if reason:
            log.append({"event": "refused", "ts": now.isoformat(), "agent": cfg.agent, "route_id": route_id,
                        "amount": str(amount), "reason": reason})
            raise CallPathError(f"the vault would deny this payment ({reason}); no approval was made")
        digest = quote_hash(response)
        args = callpath.approve_intent_args(cfg, route_id, deposit, amount_near, minutes, digest, now_ns=_ns(now))
        expires = now + datetime.timedelta(minutes=minutes)
        if record:
            log.append({"event": "approval_sent", "ts": now.isoformat(), "agent": cfg.agent, "route_id": route_id,
                        "amount": str(amount), "deposit_address": deposit, "quote_hash": digest, "expires_ns": str(_ns(expires))})
    extra = [
        f"deposit account and hash come from the saved quote ({iso(quoted_at)}), not from the command line: hash {digest}",
        f"approval lifetime {minutes} min, expires {iso(expires)} (UTC); quote deadline {iso(deadline)}",
    ]
    return args, extra


def prepare_spend(
    cfg: Config, log: GuardianLog, route_id: int, amount_near: str, *, record: bool, now: datetime.datetime | None = None,
) -> tuple[list[str], list[str]]:
    """Build `spend_intent` with the deposit account of the last approved quote. A typed account is refused earlier."""
    now = now or utc_now()
    amount = int(near_to_yocto(amount_near))
    entries = log.entries()
    sent = [e for e in entries if e.get("event") == "approval_sent" and e.get("agent") == cfg.agent and e.get("route_id") == route_id]
    if not sent:
        raise CallPathError("no approval in the guardian log for this route: run make intents-approve first")
    approval = sent[-1]
    if str(approval.get("amount")) != str(amount):
        raise CallPathError(f"the last approval is for {callpath.yocto_to_near(approval.get('amount', 0))} NEAR, not {amount_near} NEAR")
    deposit = approval.get("deposit_address")
    if not isinstance(deposit, str) or not HEX64.fullmatch(deposit):
        raise CallPathError("the logged approval has no deposit address of the expected shape")
    try:
        expires_ns = int(approval["expires_ns"])
    except (KeyError, ValueError, TypeError) as exc:
        raise CallPathError("the logged approval has no valid expiry") from exc
    if expires_ns <= _ns(now):
        raise CallPathError(f"the last approval expired at {callpath.utc_text(expires_ns)}: ask for a new quote and approval")
    if record:
        log.append({"event": "spend_sent", "ts": now.isoformat(), "agent": cfg.agent, "route_id": route_id,
                    "amount": str(amount), "deposit_address": deposit, "quote_hash": approval.get("quote_hash")})
    args = callpath.spend_intent_args(cfg, route_id, deposit, amount_near)
    extra = [f"deposit account comes from the last approved quote (hash {approval.get('quote_hash')}); approval expires {callpath.utc_text(expires_ns)}"]
    return args, extra
