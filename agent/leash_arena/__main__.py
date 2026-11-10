"""`python3 -m leash_arena --demo`: the page on this machine only, with a scripted agent."""

from __future__ import annotations

import argparse

from .demo import demo_arena
from .server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leash_arena", description="Arena page preview")
    parser.add_argument("--demo", action="store_true", required=True,
                        help="scripted agent and fake vault; the server listens on 127.0.0.1 only")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    server = make_server(demo_arena(), "127.0.0.1", args.port, mode="demo")
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
