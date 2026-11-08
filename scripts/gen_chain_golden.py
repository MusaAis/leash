#!/usr/bin/env python3
"""Writes contract/testdata/chain_golden.json from synthetic event data.

Lines have the shape the contract logs: standard, version, event, data, with
the data fields in the order the contract writes them. The version comes from
contract/Cargo.toml. The file is generated, never edited by hand. Run without
arguments to rewrite it; `build()` returns the same data for comparison.
"""
import hashlib
import json
import os
import re
import sys

GENESIS = bytes(32)
QUOTE_HASH = "ab" * 32
OUTPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "contract",
    "testdata",
    "chain_golden.json",
)


CARGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "contract", "Cargo.toml")


def contract_version() -> str:
    with open(CARGO, encoding="utf-8") as handle:
        match = re.search(r'^version = "([^"]+)"', handle.read(), re.M)
    if not match:
        raise SystemExit("no version in contract/Cargo.toml")
    return match.group(1)


def event(name: str, data: dict) -> str:
    body = {"standard": "leash", "version": contract_version(), "event": name, "data": [data]}
    return json.dumps(body, separators=(",", ":"))


def head_after(lines: list[str]) -> str:
    head = GENESIS
    for line in lines:
        head = hashlib.sha256(head + line.encode("utf-8")).digest()
    return head.hex()


def build() -> dict:
    steps = [
        ("spend_allowed", {"agent": "bob.test", "to": "carol.test", "amount": "1000000", "memo": "ok"}),
        ("spend_denied", {"agent": "bob.test", "to": "carol.test", "amount": "3000000", "reason": "over_per_tx_cap", "memo": ""}),
        ("intent_sent", {"agent": "bob.test", "route_id": 0, "deposit_account": "dep.test", "amount": "1000000", "quote_hash": QUOTE_HASH}),
        ("intent_denied", {"agent": "bob.test", "route_id": 0, "deposit_account": "dep.test", "amount": "2000000", "reason": "no_approval"}),
    ]
    lines = [event(name, data) for name, data in steps]
    rows = []
    for index, (name, data) in enumerate(steps):
        rows.append(
            {
                "outcome": name,
                "data": data,
                "line": lines[index],
                "head": head_after(lines[: index + 1]),
            }
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
        json.dump(build(), handle, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
