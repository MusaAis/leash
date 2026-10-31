"""Command line: python3 -m leash_agent {check,run,attack}."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .agent import Agent
from .attacks import run_suite
from .brain import Brain, list_models
from .chain import ChainError, Vault
from .config import Config


def main(argv: list[str] | None = None) -> int:
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

    cfg = Config.from_env()
    vault = Vault(cfg)
    try:
        if args.command == "check":
            allowed, denied = vault.stats()
            print(f"vault: {cfg.vault}\nagent: {cfg.agent}\npayee: {cfg.payee}")
            print(f"model: {cfg.ai_model} (key set: {bool(cfg.ai_api_key)})")
            print(f"counters: allowed={allowed} denied={denied}")
            return 0
        if args.command == "models":
            for model_id, price in list_models(cfg):
                print(f"{model_id}  {price}".rstrip())
            return 0
        if args.command == "run":
            outcome = Agent(Brain(cfg), vault).handle(args.task, dry_run=args.dry_run)
            print(outcome.summary() if outcome.result or outcome.decision.action != "spend" else "dry run complete")
            return 0
        brain = Brain(cfg) if args.mode == "llm" else None
        reports = [run_suite(cfg, vault, args.mode, brain)]
        if args.mode == "llm":
            reports.append(run_suite(cfg, vault, args.mode, brain, authorized=True))
        for report in reports:
            print()
            print(report.render())
        if brain is not None:
            print(f"\nmodel calls used: {brain.calls} (limit {brain.max_calls})")
        if args.json_path:
            with open(args.json_path, "w") as handle:
                json.dump([[r.__dict__ for r in rep.rows] for rep in reports], handle, indent=2)
        if any(rep.lost for rep in reports):
            return 2
        return 3 if any(rep.control_ok is False for rep in reports) else 0
    except (ChainError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
