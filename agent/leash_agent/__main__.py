"""Command line: python3 -m leash_agent {check,run,attack}."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .agent import Agent
from .attacks import build_attacks, run_suite
from .brain import Brain, list_models, missing_model_note
from .chain import ChainError, Vault, near_to_yocto
from .config import Config, EnvFileError
from .guardian import EnvRoutes, Guardian, GuardianLog
from .intents import IntentsError, OneClick, QuoteRejected, find_tokens, iso, utc_now
from .preflight import PolicySim, precheck

MAX_SAMPLES = 10


def intents_tokens(symbol, chain, oneclick) -> int:
    rows = find_tokens(oneclick.tokens(), symbol, chain)
    for token in sorted(rows, key=lambda t: (str(t.get("blockchain")), str(t.get("symbol")))):
        print(f"{token.get('blockchain')}\t{token.get('symbol')}\tdecimals={token.get('decimals')}\tprice={token.get('price')}\t{token['assetId']}")
    print(f"\n{len(rows)} token(s)")
    return 0 if rows else 1


def redact(response, secret: str) -> str:
    """Pretty JSON of an answer with the recipient address replaced, so it can be pasted in chat."""
    text = json.dumps(response, indent=2)
    return text.replace(secret, "<RECIPIENT>") if secret else text


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
        print(f"REJECTED by the guardian: {exc}\n\nraw response (recipient hidden):\n{redact(exc.response, routes.get(cfg.agent, 0).recipient)}")
        print(f"\nchecked at {iso(utc_now())}. The full answer is in the private guardian log.")
        return 1
    v, hidden = issued.verified, routes.get(cfg.agent, 0).recipient
    print(f"request sent (dry): {redact(issued.request.body(), hidden)}")
    print(f"\nverified: amountIn={v.amount_in} amountOut={v.amount_out} minAmountOut={v.min_amount_out} timeEstimate={v.time_estimate}")
    print(f"\nraw response (recipient hidden):\n{redact(issued.response, hidden)}")
    print(f"\nchecked at {iso(utc_now())}. The recipient is hidden above and lives only in your .env.")
    return 0


def main(argv: list[str] | None = None, *, cfg=None, vault=None, brain=None, models_fn=list_models, oneclick=None) -> int:
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
    args = parser.parse_args(argv)

    try:
        cfg = cfg or Config.from_env()
    except EnvFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    vault = vault or Vault(cfg)
    pays = (args.command == "attack") or (args.command == "run" and not args.dry_run)
    if pays and cfg.network != "testnet" and not os.environ.get("LEASH_ALLOW_NON_TESTNET"):
        print(
            f"error: this command can pay real funds and the network is {cfg.network}, not testnet. "
            "Set LEASH_ALLOW_NON_TESTNET=1 only if that is intended.",
            file=sys.stderr,
        )
        return 1
    try:
        if args.command == "intents-tokens":
            return intents_tokens(args.symbol, args.chain, oneclick or OneClick.from_env())
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
        print(f"error: {type(exc).__name__}: {exc}" if not isinstance(exc, (ChainError, RuntimeError)) else f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
