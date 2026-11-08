#!/usr/bin/env python3
"""Prints the gas burnt per receipt of every transaction hash found in a text file.

Usage: tx_gas.py RPC_URL SIGNER TEXT_FILE
"""
import sys

import collect_decisions as cd


def gas_rows(result: dict) -> list[tuple[str, float]]:
    """(executor, Tgas burnt) for the transaction and each receipt, in order."""
    rows = [(result["transaction_outcome"]["outcome"]["executor_id"], result["transaction_outcome"]["outcome"]["gas_burnt"] / 1e12)]
    for item in result["receipts_outcome"]:
        rows.append((item["outcome"]["executor_id"], item["outcome"]["gas_burnt"] / 1e12))
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    url, signer, path = argv[1:]
    try:
        for tx in cd.tx_hashes(open(path, encoding="utf-8").read()):
            result = cd.rpc(url, "EXPERIMENTAL_tx_status", {"tx_hash": tx, "sender_account_id": signer, "wait_until": "FINAL"})
            rows = gas_rows(result)
            print(f"{tx}: total {sum(g for _, g in rows):.3f} Tgas")
            for executor, gas in rows:
                print(f"  {executor}: {gas:.3f} Tgas")
    except (OSError, RuntimeError, KeyError, ValueError) as error:
        print(f"tx_gas ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    sys.exit(main(sys.argv))
