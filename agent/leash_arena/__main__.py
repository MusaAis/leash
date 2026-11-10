"""`python3 -m leash_arena --demo` previews the page with a scripted agent; `--live` runs the real Arena."""

from __future__ import annotations

import argparse
import os
import sys

from leash_agent.config import Config

from .demo import demo_arena
from .live import LiveError, Settings, build_live_arena
from .server import make_server


def _live(args) -> int:
    try:
        settings = Settings.from_env()
        arena = build_live_arena(settings, Config.from_env())
        server = make_server(arena, "127.0.0.1", args.port, proxy_header=settings.proxy_header, mode="live",
                             allowed_origins=settings.origins)
    except (LiveError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    tiers = ", ".join(f"{name}={tier.vault}" for name, tier in arena.tiers.items())
    print(f"arena: {tiers}; network testnet; model budget cap {settings.cap_usd} USD; "
          f"stop file {'present' if arena._flag_set() else 'absent'}; origins {len(settings.origins)}")
    if args.check:
        server.server_close()
        return 0
    print(f"live arena on http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leash_arena", description="The Arena server")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="scripted agent and fake vault, this machine only")
    mode.add_argument("--live", action="store_true", help="real model and vaults on testnet, behind a proxy")
    parser.add_argument("--check", action="store_true", help="with --live: build everything, print it, do not serve")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    if args.check and not args.live:
        parser.error("--check needs --live")
    if args.live:
        args.port = 8081 if args.port is None else args.port
        return _live(args)
    port = 8080 if args.port is None else args.port
    server = make_server(demo_arena(), "127.0.0.1", port, mode="demo")
    print(f"demo page on http://127.0.0.1:{server.server_address[1]}/  (Ctrl+C stops it)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
