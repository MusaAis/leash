"""Command line: python3 -m leash_agent {check,run,attack}."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .agent import Agent
from .attacks import build_attacks, run_suite
from . import callpath, keys, swap
from .brain import Brain, list_models, missing_model_note
from .chain import ChainError, Vault, near_to_yocto
from .chain import run_near
from .config import Config, EnvFileError
from .guard import GuardError, check as guard_check
from .guardian import EnvRoutes, Guardian, GuardianLog, Refused
from .intents import IntentsError, OneClick, QuoteRejected, find_tokens, iso, utc_now
from .preflight import PolicySim, precheck
from .probe import address_kind, wet_probe
from .redact import scrub
from .signature import EXTRA_MODEL, check_signature, describe
from .storage import check_storage

MAX_SAMPLES = 10


def intents_tokens(symbol, chain, oneclick) -> int:
    rows = find_tokens(oneclick.tokens(), symbol, chain)
    for token in sorted(rows, key=lambda t: (str(t.get("blockchain")), str(t.get("symbol")))):
        print(f"{token.get('blockchain')}\t{token.get('symbol')}\tdecimals={token.get('decimals')}\tprice={token.get('price')}\t{token['assetId']}")
    print(f"\n{len(rows)} token(s)")
    return 0 if rows else 1


def redact(response, secret: str) -> str:
    """Pretty JSON of an answer with the private values hidden, so it can be pasted in chat."""
    return scrub(json.dumps(response, indent=2), ((secret, "<RECIPIENT>"),))


def intents_dry(cfg, vault, amount_near, oneclick) -> int:
    """A dry quote of a small amount: no deposit address and no funds. Prints the raw answer as evidence."""
    amount = int(near_to_yocto(amount_near))
    asset = os.environ.get("LEASH_ROUTE_DEST_ASSET", "")
    if not asset:  # default: USDC on Base, exactly one match
        found = find_tokens(oneclick.tokens(), "USDC", "base")
        if len(found) != 1:
            raise IntentsError(f"expected exactly one USDC on base in the live token list, found {len(found)}; set LEASH_ROUTE_DEST_ASSET")
        asset = found[0]["assetId"]
    routes = EnvRoutes.from_env(asset)
    guardian = Guardian(
        agent=cfg.agent, vault_account=cfg.vault, snapshot=vault.snapshot, routes=routes,
        oneclick=oneclick, log=GuardianLog.from_env(), other_accounts=(cfg.owner,),
    )
    try:
        issued = guardian.dry_quote(0, amount, refund_to=os.environ.get("LEASH_DRY_REFUND_TO") or None)
    except QuoteRejected as exc:  # evidence: show what 1Click said, with the recipient hidden
        print(f"REJECTED by the guardian: {scrub(str(exc))}\n\nraw response (recipient hidden):\n{redact(exc.response, routes.get(cfg.agent, 0).recipient)}")
        print(f"\nchecked at {iso(utc_now())}. The full answer is in the private guardian log.")
        return 1
    v, hidden = issued.verified, routes.get(cfg.agent, 0).recipient
    print(f"request sent (dry): {redact(issued.request.body(), hidden)}")
    print(f"\nverified: amountIn={v.amount_in} amountOut={v.amount_out} minAmountOut={v.min_amount_out} timeEstimate={v.time_estimate}")
    print(f"\nraw response (recipient hidden):\n{redact(issued.response, hidden)}")
    print(f"\nchecked at {iso(utc_now())}. The recipient is hidden above and lives only in your .env.")
    return 0


def intents_wet_probe(cfg, amount_near, oneclick) -> int:
    """One real quote, never paid. Prints the deposit address and its format."""
    asset = os.environ.get("LEASH_ROUTE_DEST_ASSET", "")
    if not asset:
        found = find_tokens(oneclick.tokens(), "USDC", "base")
        if len(found) != 1:
            raise IntentsError(f"expected exactly one USDC on base in the live token list, found {len(found)}; set LEASH_ROUTE_DEST_ASSET")
        asset = found[0]["assetId"]
    recipient = EnvRoutes.from_env(asset).get(cfg.agent, 0).recipient
    refund_to = os.environ.get("LEASH_DRY_REFUND_TO", "")
    if not refund_to:
        raise IntentsError("set LEASH_DRY_REFUND_TO to an existing NEAR account you control")
    log = GuardianLog.from_env()
    try:
        body, v, response = wet_probe(oneclick, log, dest_asset=asset, recipient=recipient, refund_to=refund_to,
                                      amount=int(near_to_yocto(amount_near)), forbidden=(cfg.vault, cfg.owner))
    except QuoteRejected as exc:
        print(f"REJECTED: {scrub(str(exc))}\n\nraw response (recipient hidden):\n{redact(exc.response, recipient)}")
        return 1
    print(f"request sent (wet): {redact(body, recipient)}")
    print(f"\nverified: amountIn={v.amount_in} amountOut={v.amount_out} minAmountOut={v.min_amount_out} timeEstimate={v.time_estimate}")
    print(f"deposit address: {v.deposit_address}\nformat: {address_kind(v.deposit_address)}\naddress active until: {iso(v.deadline)}")
    print(f"\nraw response (recipient hidden):\n{redact(response, recipient)}")
    print("\nDO NOT SEND ANY FUNDS TO THIS ADDRESS. Nothing was deposited; the address stays active until the time above.")
    return 0


def wrap_storage(accounts: list[str], post=None) -> int:
    """Read-only: wrap.near storage bounds and balances. Defaults: the last probe's deposit address and the refund account."""
    accounts = list(accounts)
    for entry in reversed(GuardianLog.from_env().entries()):
        if entry.get("event") == "probe_quote":
            accounts.append(entry["response"]["quote"]["depositAddress"])
            break
    refund = os.environ.get("LEASH_DRY_REFUND_TO", "")
    if refund:
        accounts.append(refund)
    results = check_storage(list(dict.fromkeys(accounts)), **({"post": post} if post else {}))
    print(f"read-only checks on wrap.near at {iso(utc_now())} (no transaction, no key):")
    for name, value in results.items():
        print(scrub(f"{name}: {json.dumps(value)}"))
    return 0


def agent_signature(cfg, models, check=check_signature) -> int:
    """Signature kind test: ask each model one question and report the signature kind. Never verifies the signature."""
    if not cfg.ai_api_key:
        raise RuntimeError("set NEAR_AI_API_KEY in .env")
    log = GuardianLog.from_env()
    print(f"signature test at {iso(utc_now())} (the signature itself is not verified)")
    for model in models or [cfg.ai_model, EXTRA_MODEL]:
        print(describe(check(model, cfg.ai_api_key, cfg.ai_base_url, log=log)))
    return 0


def intents_quote(cfg, args, oneclick, runner) -> int:
    """Guardian: one real quote for the agent's route, verified and saved in the private log. Never paid."""
    if cfg.network == "testnet":
        print("error: 1Click has no testnet, so there is nothing to quote here. Use the rehearsal commands with --deposit-account.", file=sys.stderr)
        return 1
    try:
        callpath.guard_or_raise(cfg, [cfg.vault, cfg.owner, cfg.agent, cfg.guardian])
    except callpath.CallPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.yes:
        print("error: a real quote creates a deposit address (never pay it by hand). Add --yes.", file=sys.stderr)
        return 1
    asset = os.environ.get("LEASH_ROUTE_DEST_ASSET", "")
    if not asset:
        found = find_tokens(oneclick.tokens(), "USDC", "base")
        if len(found) != 1:
            raise IntentsError(f"expected exactly one USDC on base in the live token list, found {len(found)}; set LEASH_ROUTE_DEST_ASSET")
        asset = found[0]["assetId"]
    routes = EnvRoutes.from_env(asset)
    guardian = Guardian(
        agent=cfg.agent, vault_account=cfg.vault, snapshot=Vault(cfg, runner=runner).snapshot, routes=routes,
        oneclick=oneclick, log=GuardianLog.from_env(), other_accounts=(cfg.owner, cfg.guardian, cfg.wnear),
    )
    try:
        issued = guardian.quote(args.route, int(near_to_yocto(args.amount)))
    except QuoteRejected as exc:
        print(f"REJECTED by the guardian: {scrub(str(exc))}\n\nraw response (recipient hidden):\n{redact(exc.response, routes.get(cfg.agent, 0).recipient)}", file=sys.stderr)
        return 1
    except (Refused, IntentsError) as exc:
        print(f"error: {scrub(str(exc))}", file=sys.stderr)
        return 1
    v = issued.verified
    print(f"verified: amountIn={v.amount_in} amountOut={v.amount_out} minAmountOut={v.min_amount_out} timeEstimate={v.time_estimate}")
    print(f"saved in the private guardian log at {iso(utc_now())}; deposit address {v.deposit_address}")
    print("Nothing was deposited. Do not send funds to this address by hand. Next: make intents-approve, then make intents-spend.")
    return 0


def intents_call(cfg, args, runner) -> int:
    """The call path: owner config and routes, guardian approval, agent spend, read-only status."""
    cmd = args.command
    if cmd == "intents-status":
        accounts, raw_answers = [cfg.vault, cfg.agent], {}

        def tap(argv):  # keep the raw answer of each view, so the balance is read from the same call that is printed
            raw_answers[argv[5]] = runner(argv)
            return raw_answers[argv[5]]

        for name, argv in callpath.status_commands(cfg):
            try:
                output = callpath.execute(cfg, argv, accounts, confirmed=True, runner=tap)
            except callpath.CallPathError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"-- {name}\n{output}")
        try:
            print("-- balance around the last spend\n" + "\n".join(swap.balance_report(GuardianLog.from_env(), cfg, swap.parse_balance(raw_answers.get("get_available_balance", "")))))
        except IntentsError as exc:
            print(f"-- balance around the last spend\nnot shown: {scrub(str(exc))}")
        return 0
    extra = []
    try:
        if cmd == "intents-keys":
            return keys.run(cfg, confirmed=args.yes, runner=runner, aside=args.aside)
        if cmd == "intents-config":
            callpath.require_vault_name(cfg, args.confirm_vault, args.yes)
            built, accounts = callpath.set_intents_config_args(cfg), [cfg.vault, cfg.owner, cfg.guardian, cfg.wnear]
        elif cmd == "intents-routes":
            callpath.require_vault_name(cfg, args.confirm_vault, args.yes)
            built = callpath.set_routes_args(cfg, os.environ.get("LEASH_ROUTE_DEST_ASSET", ""), os.environ.get("LEASH_ROUTE_RECIPIENT", ""))
            accounts = [cfg.vault, cfg.owner, cfg.agent]
        elif cmd == "intents-approve":
            if cfg.network == "testnet":
                if not args.deposit_account:
                    raise callpath.CallPathError("on testnet pass --deposit-account (1Click has no testnet, so there is no saved quote)")
                built = callpath.approve_intent_args(
                    cfg, args.route, args.deposit_account, args.amount, args.minutes or 20, args.quote_hash or callpath.REHEARSAL_QUOTE_HASH)
                accounts = [cfg.vault, cfg.guardian, cfg.agent, args.deposit_account]
            else:
                accounts = [cfg.vault, cfg.guardian, cfg.agent]
                callpath.guard_or_raise(cfg, accounts)  # first, before anything is read or recorded
                if args.deposit_account or args.quote_hash:
                    raise callpath.CallPathError("outside testnet the deposit address and the hash come only from the guardian's saved quote; do not pass them")
                built, extra = swap.prepare_approval(
                    cfg, Vault(cfg, runner=runner), GuardianLog.from_env(), args.route, args.amount, record=args.yes, max_minutes=args.minutes)
        else:
            if cfg.network == "testnet":
                if not args.deposit_account:
                    raise callpath.CallPathError("on testnet pass --deposit-account")
                built, accounts = callpath.spend_intent_args(cfg, args.route, args.deposit_account, args.amount), [cfg.vault, cfg.agent, args.deposit_account]
            else:
                accounts = [cfg.vault, cfg.agent]
                callpath.guard_or_raise(cfg, accounts)
                if args.deposit_account:
                    raise callpath.CallPathError("outside testnet the deposit account comes only from the last approved quote; do not pass one")
                built, extra = swap.prepare_spend(cfg, GuardianLog.from_env(), args.route, args.amount, record=args.yes)
        if cmd == "intents-spend" and args.yes:
            balance = swap.read_balance(cfg, runner)
            if balance is None and cfg.network != "testnet":
                raise callpath.CallPathError("the vault balance could not be read, so nothing was sent")
            if balance is None:
                extra = [*extra, "note: the vault balance before the spend could not be read (testnet), so none is saved"]
            else:
                swap.record_balance_before(cfg, GuardianLog.from_env(), built, balance)
                extra = [*extra, f"vault available balance before the spend, saved in the private log: {callpath.yocto_to_near(balance)} NEAR"]
        output = callpath.execute(cfg, built, accounts, confirmed=args.yes, runner=runner, extra=extra)
    except (callpath.CallPathError, IntentsError, ChainError, ValueError) as exc:
        print(f"error: {scrub(str(exc))}", file=sys.stderr)
        return 1
    if output is not None:
        print(output)
    return 0


def main(argv: list[str] | None = None, *, cfg=None, vault=None, brain=None, models_fn=list_models, oneclick=None, runner=run_near) -> int:
    parser = argparse.ArgumentParser(prog="leash_agent", description="LEASH agent runtime")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="show configuration and vault counters")
    sub.add_parser("models", help="list the models available to the API key")
    run = sub.add_parser("run", help="give the agent one task")
    run.add_argument("task")
    run.add_argument("--dry-run", action="store_true", help="decide but do not call the vault")
    attack = sub.add_parser("attack", help="run the attack suite against the vault")
    attack.add_argument("--mode", choices=["compromised", "llm"], default="compromised")
    attack.add_argument("--json", dest="json_path", help="write the report to this file")
    attack.add_argument(
        "--samples", type=int, default=1,
        help="llm mode: ask the model this many times per attack; extra samples only record the decision "
        "(set LEASH_TEMPERATURE above 0, or every sample is the same)",
    )
    toks = sub.add_parser("intents-tokens", help="list tokens supported by 1Click (live)")
    toks.add_argument("--symbol")
    toks.add_argument("--chain")
    dry = sub.add_parser("intents-dry", help="ask 1Click for a dry quote (no deposit address, no funds)")
    dry.add_argument("--amount", required=True, help="NEAR to swap, for example 0.5")
    wet = sub.add_parser("intents-wet-probe", help="one real quote to read the deposit address (never pay it)")
    wet.add_argument("--amount", default="0.1", help="NEAR, at most 0.15 (default 0.1)")
    wet.add_argument("--yes", action="store_true", help="confirm that this asks for a real quote")
    ws = sub.add_parser("wrap-storage", help="read-only: wrap.near storage bounds and balances")
    ws.add_argument("--account", action="append", default=[], help="extra account to check (repeatable)")
    gd = sub.add_parser("guard", help="check that every account fits the network (mainnet needs LEASH_ALLOW_MAINNET=1)")
    gd.add_argument("--network", required=True)
    gd.add_argument("accounts", nargs="+")
    sub.add_parser("intents-status", help="read-only: approvals, stats, policy counters and vault balance")
    keyp = sub.add_parser("intents-keys", help="testnet: add the guardian and agent function-call keys (plan only without --yes); mainnet: read-only check")
    keyp.add_argument("--yes", action="store_true")
    keyp.add_argument("--aside", action="store_true", help="testnet: after adding, move creation-key files off this machine (never deletes)")
    for name, text in (("intents-config", "owner: set the wNEAR contract and the guardian (1 yocto; revokes approvals)"),
                       ("intents-routes", "owner: set route 0 from LEASH_ROUTE_DEST_ASSET and LEASH_ROUTE_RECIPIENT (1 yocto)")):
        owner = sub.add_parser(name, help=text)
        owner.add_argument("--yes", action="store_true")
        owner.add_argument("--confirm-vault", help="the vault name, required to send")
    quote = sub.add_parser("intents-quote", help="guardian: one real quote, verified and saved in the private log (never paid)")
    quote.add_argument("--route", type=int, default=0)
    quote.add_argument("--amount", required=True, help="NEAR, for example 0.1")
    quote.add_argument("--yes", action="store_true", help="confirm that this asks for a real quote")
    appr = sub.add_parser("intents-approve", help="guardian: one single-use approval (no deposit)")
    spend = sub.add_parser("intents-spend", help="agent: spend one approval with spend_intent")
    for sp in (appr, spend):
        sp.add_argument("--route", type=int, default=0)
        sp.add_argument("--deposit-account", help="testnet only; outside testnet it comes from the saved quote")
        sp.add_argument("--amount", required=True, help="NEAR, for example 0.1")
        sp.add_argument("--yes", action="store_true", help="send; without it only the plan is printed")
    appr.add_argument("--minutes", type=int, help=f"approval lifetime, 1 to {callpath.MAX_APPROVAL_MINUTES} (testnet default 20; elsewhere an upper bound)")
    appr.add_argument("--quote-hash", help="testnet only (default: a fixed rehearsal hash); elsewhere the hash comes from the saved quote")
    sig = sub.add_parser("agent-signature", help="which signature kind does the gateway give a model's answer?")
    sig.add_argument("--model", action="append", default=[], help="model id (repeatable; default: the configured model and z-ai/glm-5.3)")
    args = parser.parse_args(argv)

    if args.command == "guard":  # pure check: no .env, no network, never reads LEASH_ALLOW_MAINNET from a file
        try:
            accounts = guard_check(args.network, args.accounts)
        except GuardError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"network {args.network}: " + ", ".join(accounts) + "\nOK")
        return 0

    try:
        cfg = cfg or Config.from_env()
    except EnvFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    vault = vault or Vault(cfg)
    pays = (args.command == "attack") or (args.command == "run" and not args.dry_run)
    if pays and cfg.network != "testnet" and os.environ.get("LEASH_ALLOW_NON_TESTNET") != "1":
        print(
            f"error: this command can pay real funds and the network is {cfg.network}, not testnet. "
            "Set LEASH_ALLOW_NON_TESTNET=1 only if that is intended.",
            file=sys.stderr,
        )
        return 1
    try:
        if args.command == "intents-quote":
            return intents_quote(cfg, args, oneclick or OneClick.from_env(), runner)
        if args.command in ("intents-status", "intents-keys", "intents-config", "intents-routes", "intents-approve", "intents-spend"):
            return intents_call(cfg, args, runner)
        if args.command == "intents-tokens":
            return intents_tokens(args.symbol, args.chain, oneclick or OneClick.from_env())
        if args.command == "agent-signature":
            return agent_signature(cfg, args.model)
        if args.command == "wrap-storage":
            return wrap_storage(args.account)
        if args.command == "intents-wet-probe":
            if not args.yes:
                print("error: a wet quote is a real quote. Add --yes. It is never paid.", file=sys.stderr)
                return 1
            return intents_wet_probe(cfg, args.amount, oneclick or OneClick.from_env())
        if args.command == "intents-dry":
            return intents_dry(cfg, vault, args.amount, oneclick or OneClick.from_env())
        if args.command == "check":
            allowed, denied = vault.stats()
            print(f"vault: {cfg.vault}\nagent: {cfg.agent}\npayee: {cfg.payee}")
            print(f"model: {cfg.ai_model} (key set: {bool(cfg.ai_api_key)})")
            print(f"counters: allowed={allowed} denied={denied}")
            return 0
        if args.command == "models":
            rows = models_fn(cfg)
            for model_id, price in rows:
                print(f"{model_id}  {price}".rstrip())
            note = missing_model_note(rows, cfg.ai_model)
            if note:
                print(f"\n{note}")
            return 0
        if args.command == "run":
            outcome = Agent(Brain(cfg), vault).handle(args.task, dry_run=args.dry_run)
            print(outcome.summary() if outcome.result or outcome.decision.action != "spend" else "dry run complete")
            return 0
        if not 1 <= args.samples <= MAX_SAMPLES or (args.mode != "llm" and args.samples != 1):
            raise RuntimeError(f"--samples must be 1 to {MAX_SAMPLES}, and more than 1 needs --mode llm")
        brain = (brain or Brain(cfg)) if args.mode == "llm" else None
        snap = vault.snapshot()
        attacks = len(build_attacks(cfg))
        problems = precheck(snap, cfg, args.mode, attacks)
        if brain is not None:
            calls = 1 + 2 * attacks * args.samples
            if calls > brain.max_calls:
                problems.append(
                    f"this run needs {calls} model calls and LEASH_MAX_MODEL_CALLS is {brain.max_calls}"
                )
        if problems:
            print("Preflight failed. Nothing was sent to the vault and no model call was made:")
            for problem in problems:
                print(f"  - {problem}")
            return 3
        sim = PolicySim(snap)
        reports = [run_suite(cfg, vault, args.mode, brain, sim=sim, samples=args.samples)]
        if args.mode == "llm":
            reports.append(run_suite(cfg, vault, args.mode, brain, authorized=True, samples=args.samples, sim=sim))
        for report in reports:
            print()
            print(report.render())
        if args.mode == "llm":
            print(
                f"\noverall: agent fooled: {sum(rep.fooled for rep in reports)}  "
                f"funds lost: {sum(rep.funds_lost for rep in reports)}"
            )
        if brain is not None:
            print(
                f"\nmodel calls used: {brain.calls} (limit {brain.max_calls}), "
                f"tokens used: {brain.tokens_used} (limit {brain.max_total_tokens})"
            )
        if args.json_path:
            with open(args.json_path, "w") as handle:
                json.dump([[r.__dict__ for r in rep.rows] for rep in reports], handle, indent=2)
        if any(rep.funds_lost for rep in reports):
            return 2
        return 3 if any(rep.invalid for rep in reports) else 0
    except Exception as exc:  # report any failure as a plain error, never a traceback
        message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, (ChainError, RuntimeError)) else str(exc)
        print(f"error: {scrub(message)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
