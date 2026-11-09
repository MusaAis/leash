#!/usr/bin/env python3
"""Collects the decision events of a vault from transaction outcomes, in the order of the input, for replay_head.py.

Usage: collect_decisions.py RPC_URL VAULT SIGNER TX_FILE EVENTS_FILE [--head]

TX_FILE: text; every transaction hash found in it, in order of appearance, was sent by SIGNER.
Only events of succeeded receipts executed on VAULT that extend the chain are kept: a failed receipt can leave a log line
whose head change was reverted. --head also prints the head the vault reports (the `get_head_hash` view).
"""
import base64
import json
import re
import sys
import urllib.request

PREFIX = "EVENT_JSON:"
DECISIONS = frozenset({"spend_allowed", "spend_denied", "intent_sent", "intent_denied"})
HASH_RE = re.compile(r"(?<![:\w])[1-9A-HJ-NP-Za-km-z]{43,44}\b")  # not after a colon: public keys are ed25519:<44 characters>


def rpc(url: str, method: str, params) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": "leash", "method": method, "params": params}).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the URL comes from the operator
        answer = json.load(response)
    if "error" in answer:
        raise RuntimeError(f"{method}: {answer['error'].get('name') or answer['error']}")
    return answer["result"]


def tx_hashes(text: str) -> list[str]:
    seen, out = set(), []
    for found in HASH_RE.findall(text):
        if found not in seen:
            seen.add(found)
            out.append(found)
    return out


def decision_lines(result: dict, vault: str) -> list[str]:
    """The chain-extending event lines of one transaction, from receipts that succeeded on the vault."""
    lines = []
    for item in result["receipts_outcome"]:
        outcome = item["outcome"]
        if outcome["executor_id"] != vault or "Failure" in outcome["status"] or "Unknown" in outcome["status"]:
            continue
        for log in outcome["logs"]:
            if not log.startswith(PREFIX):
                continue
            try:
                event = json.loads(log[len(PREFIX):]).get("event")
            except ValueError:
                continue
            if event in DECISIONS:
                lines.append(log)
    return lines


def collect(fetch, vault: str, signer: str, hashes: list[str]) -> list[str]:
    lines: list[str] = []
    for tx in hashes:
        result = fetch("EXPERIMENTAL_tx_status", {"tx_hash": tx, "sender_account_id": signer, "wait_until": "FINAL"})
        lines.extend(decision_lines(result, vault))
    return lines


def read_head(fetch, vault: str) -> str:
    result = fetch("query", {"request_type": "call_function", "finality": "final", "account_id": vault,
                             "method_name": "get_head_hash", "args_base64": base64.b64encode(b"{}").decode()})
    return json.loads(bytes(result["result"]).decode())


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--head"]
    if len(args) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    url, vault, signer, tx_file, out_file = args
    fetch = lambda method, params: rpc(url, method, params)  # noqa: E731
    try:
        hashes = tx_hashes(open(tx_file, encoding="utf-8").read())
        lines = collect(fetch, vault, signer, hashes)
        head = read_head(fetch, vault) if "--head" in argv else None
    except (OSError, RuntimeError, KeyError, ValueError) as error:
        print(f"collect ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    with open(out_file, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(line + "\n" for line in lines))
    print(f"{len(hashes)} transactions, {len(lines)} decision events written to {out_file}")
    if head:
        print(f"head {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
