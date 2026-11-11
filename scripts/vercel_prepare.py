#!/usr/bin/env python3
"""Usage: scripts/vercel_prepare.py <https API origin> [out dir]
Copies the Arena page to <out dir> (default build/vercel) with the API origin set in the page and a
vercel.json that allows the page to call only that origin. Deploy the out dir as a static site."""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = re.compile(r"^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")
META = '<meta name="leash-api" content="">'


def build(origin: str, out: Path) -> None:
    if not ORIGIN.match(origin):
        raise SystemExit("the API origin must be https://host[:port] in lower case, with no path")
    html = (ROOT / "web" / "index.html").read_text()
    if html.count(META) != 1:
        raise SystemExit("web/index.html has no empty leash-api meta tag")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("arena.css", "arena.js"):
        shutil.copyfile(ROOT / "web" / name, out / name)
    (out / "index.html").write_text(html.replace(META, f'<meta name="leash-api" content="{origin}">'))
    policy = ("default-src 'none'; script-src 'self'; style-src 'self'; "
              f"connect-src {origin}; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    headers = {"Content-Security-Policy": policy, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
               "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin",
               "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
    config = {"headers": [{"source": "/(.*)", "headers": [{"key": k, "value": v} for k, v in headers.items()]}]}
    (out / "vercel.json").write_text(json.dumps(config, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    target = Path(sys.argv[2]) if len(sys.argv) == 3 else ROOT / "build" / "vercel"
    build(sys.argv[1], target)
    print(f"wrote {target}")
