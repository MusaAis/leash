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
from .chain import ChainError, Vault
from .config import Config
from .preflight import PolicySim, precheck

MAX_SAMPLES = 10


def main(argv: list[str] | None = None, *, cfg=None, vault=None, brain=None, models_fn=list_models) -> int:
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
    args = parser.parse_args(argv)

    cfg = cfg or Config.from_env()
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
