"""Approval and spend outside testnet: the deposit address and the quote hash come only from the guardian's saved quote."""

from __future__ import annotations

import datetime
import hashlib
import json
import re

from . import callpath
from .callpath import CallPathError
from .chain import ChainError, Vault, near_to_yocto, parse_call_output
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


def _failed_after(entries: list[dict], index: int, what: str, deposit: object) -> bool:
    """True if a later `send_failed` entry says that this approval or spend never reached the chain."""
    return any(e.get("event") == "send_failed" and e.get("what") == what and e.get("deposit_address") == deposit for e in entries[index + 1:])


def record_approval(cfg: Config, log: GuardianLog, built: list[str], now: datetime.datetime | None = None) -> None:
    """Write `approval_sent` from the arguments about to be sent. Called immediately before the send."""
    data = callpath._payload_of(built)
    log.append({"event": "approval_sent", "ts": (now or utc_now()).isoformat(), "agent": cfg.agent, "route_id": data.get("route_id"),
                "amount": str(data.get("amount")), "deposit_address": data.get("deposit_account"), "quote_hash": data.get("quote_hash"),
                "expires_ns": str(data.get("expires_ns"))})


def record_spend(cfg: Config, log: GuardianLog, built: list[str], balance: int | None, now: datetime.datetime | None = None) -> None:
    """Write `spend_sent` and the balance before it, immediately before the send."""
    data = callpath._payload_of(built)
    log.append({"event": "spend_sent", "ts": (now or utc_now()).isoformat(), "agent": cfg.agent, "route_id": data.get("route_id"),
                "amount": str(data.get("amount")), "deposit_address": data.get("deposit_account"), "quote_hash": None})
    if balance is not None:
        record_balance_before(cfg, log, built, balance, now)


def record_send_failed(cfg: Config, log: GuardianLog, built: list[str], what: str, now: datetime.datetime | None = None) -> None:
    data = callpath._payload_of(built)
    log.append({"event": "send_failed", "what": what, "ts": (now or utc_now()).isoformat(), "agent": cfg.agent, "route_id": data.get("route_id"),
                "amount": str(data.get("amount")), "deposit_address": data.get("deposit_account")})


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
        if any(e.get("event") == "approval_sent" and e.get("deposit_address") == deposit and not _failed_after(entries, i, "approval", deposit)
               for i, e in enumerate(entries)):
            raise CallPathError("this quote was already approved; an approval is single use: ask for a new quote")
        try:
            on_chain = vault.view("get_approvals", {"agent": cfg.agent})
        except ChainError as exc:
            raise CallPathError(f"the approvals on the chain could not be read, so nothing was approved: {exc}") from exc
        if isinstance(on_chain, list) and any(isinstance(a, dict) and a.get("deposit_account") == deposit for a in on_chain):
            raise CallPathError("the chain already holds an approval for this quote's deposit account; an approval is single use: ask for a new quote")
        recent = 0
        for i, entry in enumerate(entries):
            if entry.get("event") == "approval_sent" and now - _when(entry.get("ts"), "time") < datetime.timedelta(hours=1) \
                    and not _failed_after(entries, i, "approval", entry.get("deposit_address")):
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
        if record:  # the CLI path records right before the send instead (record_approval)
            record_approval(cfg, log, args, now)
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
    sent = [e for i, e in enumerate(entries) if e.get("event") == "approval_sent" and e.get("agent") == cfg.agent and e.get("route_id") == route_id
            and not _failed_after(entries, i, "approval", e.get("deposit_address"))]
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
    args = callpath.spend_intent_args(cfg, route_id, deposit, amount_near)
    if record:  # the CLI path records right before the send instead (record_spend)
        record_spend(cfg, log, args, None, now)
    extra = [f"deposit account comes from the last approved quote (hash {approval.get('quote_hash')}); approval expires {callpath.utc_text(expires_ns)}"]
    earlier = [e for i, e in enumerate(entries) if e.get("event") == "spend_sent" and e.get("deposit_address") == deposit
               and not _failed_after(entries, i, "spend", deposit)]
    if earlier:
        extra.append(f"WARNING: an earlier spend for this approval was sent at {earlier[-1].get('ts')}; run make intents-status before sending again "
                     "(a retry is only right if that spend failed before it counted anything)")
    return args, extra


# the vault's available balance around a spend (private log, mode 0600)
def parse_balance(text: str) -> int | None:
    """The yocto amount of a `get_available_balance` answer, or None if it cannot be read."""
    try:
        value = parse_call_output(text).returned
        return int(str(value))
    except (ChainError, ValueError, TypeError):
        return None


def read_balance(cfg: Config, runner) -> int | None:
    try:
        return parse_balance(runner(callpath._view(cfg, "get_available_balance")))
    except ChainError:
        return None


def record_balance_before(
    cfg: Config, log: GuardianLog, built: list[str], balance: int, now: datetime.datetime | None = None,
) -> None:
    data = callpath._payload_of(built)
    log.append({"event": "spend_balance_before", "ts": (now or utc_now()).isoformat(), "agent": cfg.agent, "route_id": data.get("route_id"),
                "amount": str(data.get("amount")), "deposit_address": data.get("deposit_account"), "balance": str(balance)})


def _signed_near(delta: int) -> str:
    return ("-" if delta < 0 else "+") + callpath.yocto_to_near(abs(delta))


def balance_report(log: GuardianLog, cfg: Config, current: int | None) -> list[str]:
    """The saved before-value of the last spend next to the current balance. Other transactions also change it."""
    before = [e for e in log.entries() if e.get("event") == "spend_balance_before" and e.get("agent") == cfg.agent]
    if not before:
        return ["no saved balance from an earlier spend in the private log"]
    last = before[-1]
    try:
        old = int(last["balance"])
    except (KeyError, ValueError, TypeError):
        return ["the saved balance entry is damaged; fix or move it"]
    lines = [f"available balance before the last spend ({last.get('ts')}, amount {callpath.yocto_to_near(last.get('amount', 0))} NEAR): "
             f"{callpath.yocto_to_near(old)} NEAR ({old} yocto)"]
    if current is None:
        return lines + ["available balance now: could not be read"]
    delta = current - old
    lines.append(f"available balance now: {callpath.yocto_to_near(current)} NEAR ({current} yocto)")
    lines.append(f"difference: {_signed_near(delta)} NEAR ({delta} yocto)")
    lines.append("difference is 0: the batch may still be running, or nothing moved; read again in a few seconds" if delta == 0 else
                 "other transactions in between (funding, owner calls that change stored data, a refund) also change this number")
    return lines
