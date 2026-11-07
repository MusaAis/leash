#!/usr/bin/env python3
"""Writes contract/testdata/chain_golden.json from synthetic event lines.

The file is generated, never edited by hand. Run without arguments to
rewrite it; `build()` returns the same data for comparison in tests.
"""
import hashlib
import json
import os
import sys

GENESIS = bytes(32)
QUOTE_HASH = "ab" * 32
VERSION = "0.2.0"
OUTPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "contract",
    "testdata",
    "chain_golden.json",
)


def event(name: str, data: dict) -> str:
    body = {"data": [data], "event": name, "standard": "leash", "version": VERSION}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def head_after(lines: list[str]) -> str:
    head = GENESIS
    for line in lines:
        head = hashlib.sha256(head + line.encode("utf-8")).digest()
    return head.hex()


def build() -> dict:
    steps = [
        ("spend_allowed", {"agent": "bob.test", "amount": "1000000", "memo": "ok", "to": "carol.test"}),
        ("spend_denied", {"agent": "bob.test", "amount": "3000000", "memo": "", "reason": "over_per_tx_cap", "to": "carol.test"}),
        ("intent_sent", {"agent": "bob.test", "amount": "1000000", "deposit_account": "dep.test", "quote_hash": QUOTE_HASH, "route_id": 0}),
        ("intent_denied", {"agent": "bob.test", "amount": "2000000", "deposit_account": "dep.test", "reason": "no_approval", "route_id": 0}),
    ]
    lines = [event(name, data) for name, data in steps]
    rows = []
    for index, (name, _) in enumerate(steps):
        rows.append(
            {"outcome": name, "line": lines[index], "head": head_after(lines[: index + 1])}
        )
    tampered = list(lines)
    tampered[1] = tampered[1].replace("over_per_tx_cap", "zero_amount")
    return {
        "genesis": GENESIS.hex(),
        "steps": rows,
        "tampered": {"index": 1, "lines": tampered, "head": head_after(tampered)},
    }


def main() -> int:
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(build(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
