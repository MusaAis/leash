"""Command line: python3 -m leash_agent {check,run,attack}."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .agent import Agent
from .attacks import run_suite
from .brain import Brain, list_models, missing_model_note
from .chain import ChainError, Vault
from .config import Config


NEEDED_YOCTO = 8 * 3 * 10**23  # eight authorized 0.3 NEAR invoices


def warn_if_cap_is_low(vault: Vault) -> None:
    """The authorized-invoice scenario needs daily-cap room, or the cap hides what it tests."""
    try:
        headroom = vault.daily_headroom_yocto()
    except ChainError:
        headroom = None
    if headroom is None:
        print("WARNING: could not read the agent policy (none is set, or the vault view failed). Run make policy-testnet.")
    elif headroom < NEEDED_YOCTO:
        print(
            f"WARNING: only {headroom / 10**24:.2f} NEAR of the daily cap is left (the authorized "
            "scenario needs 2.4). Payments to the payee will be stopped by the cap, which hides "
            "what the scenario tests. Use a fresh LEASH_PREFIX or wait for the window to reset.\n"
        )


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
        brain = (brain or Brain(cfg)) if args.mode == "llm" else None
        if args.mode == "llm":
            warn_if_cap_is_low(vault)
        reports = [run_suite(cfg, vault, args.mode, brain)]
        if args.mode == "llm":
            reports.append(run_suite(cfg, vault, args.mode, brain, authorized=True))
        for report in reports:
            print()
            print(report.render())
        if brain is not None:
            print(
                f"\nmodel calls used: {brain.calls} (limit {brain.max_calls}), "
                f"tokens used: {brain.tokens_used} (limit {brain.max_total_tokens})"
            )
        if args.json_path:
            with open(args.json_path, "w") as handle:
                json.dump([[r.__dict__ for r in rep.rows] for rep in reports], handle, indent=2)
        if any(rep.lost for rep in reports):
            return 2
        failed = any(rep.control_ok is False or rep.missing_tx for rep in reports)
        return 3 if failed else 0
    except Exception as exc:  # report any failure as a plain error, never a traceback
        print(f"error: {type(exc).__name__}: {exc}" if not isinstance(exc, (ChainError, RuntimeError)) else f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
