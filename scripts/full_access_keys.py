#!/usr/bin/env python3
"""List the keys of a NEAR account that are not limited to function calls, read from the RPC as JSON.

Usage: full_access_keys.py ACCOUNT NETWORK
Prints one line `FULL_ACCESS <public key>` per such key, then `COUNT <n>`.
Exit codes: 0 no such key, 1 at least one such key, 2 the RPC answer could not be read (not verified).
Any permission other than a function-call key counts, so an unknown key type is never treated as safe.
LEASH_RPC_URL overrides the endpoint (https, or http on this machine for tests).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Public endpoints from https://docs.near.org/api/rpc/providers
RPC_URLS = {"testnet": "https://test.rpc.fastnear.com", "mainnet": "https://free.rpc.fastnear.com"}


def risky_keys(answer: object) -> list[str]:
    """Public keys whose permission is not a function-call permission. Raises ValueError on a bad answer."""
    if not isinstance(answer, dict) or "result" not in answer:
        detail = answer.get("error") if isinstance(answer, dict) else None
        raise ValueError(f"no result in the RPC answer: {json.dumps(detail)[:200] if detail else 'unexpected shape'}")
    keys = answer["result"].get("keys") if isinstance(answer["result"], dict) else None
    if not isinstance(keys, list):
        raise ValueError("the RPC answer has no key list")
    found = []
    for item in keys:
        try:
            permission = item["access_key"]["permission"]
            public_key = str(item["public_key"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"unreadable key entry: {exc!r}") from exc
        if not (isinstance(permission, dict) and "FunctionCall" in permission):
            found.append(public_key)
    return found


def rpc_url_ok(url: str) -> bool:
    """https anywhere; plain http only for the loopback host itself (never a name that merely starts with it)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


def fetch(account: str, network: str) -> object:
    url = os.environ.get("LEASH_RPC_URL") or RPC_URLS.get(network, "")
    if not rpc_url_ok(url):  # plain http: loopback only
        raise ValueError(f"no https RPC endpoint for network {network!r}; set LEASH_RPC_URL")
    body = json.dumps({
        "jsonrpc": "2.0", "id": "leash", "method": "query",
        "params": {"request_type": "view_access_key_list", "finality": "final", "account_id": account},
    }).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"RPC request failed: {exc}") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        found = risky_keys(fetch(argv[1], argv[2]))
    except ValueError as exc:
        print(f"not verified: {exc}", file=sys.stderr)
        return 2
    for key in found:
        print(f"FULL_ACCESS {key}")
    print(f"COUNT {len(found)}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
