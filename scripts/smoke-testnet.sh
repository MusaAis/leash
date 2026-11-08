#!/usr/bin/env bash
# Testnet smoke on a fresh prefix: build the vault from the wasm file, run the attack suite and one allowed and one denied
# spend_intent, then replay the decision events against the vault's head and read the gas of the intent transactions.
# Usage: LEASH_PREFIX=<fresh prefix> EXPECT_SHA=<sha256 of the wasm> scripts/smoke-testnet.sh
# Output files go to ~/leash-private/smoke-<prefix>/ (private; never commit).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh
require_testnet smoke-testnet
case "$PREFIX" in leash|leash9|leashr1|leashdemo) echo "Use a fresh prefix, not $PREFIX."; exit 1 ;; esac
: "${EXPECT_SHA:?set EXPECT_SHA to the sha256 of the wasm that must be deployed}"
GOT="$(sha256sum "$WASM" | cut -d' ' -f1)"
[ "$GOT" = "$EXPECT_SHA" ] || { echo "wasm hash mismatch: $GOT"; exit 1; }
LEASH_RPC="${LEASH_RPC:-https://test.rpc.fastnear.com}"
OUT="$HOME/leash-private/smoke-$PREFIX"; mkdir -p "$OUT"
DEPOSIT="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"

step "1/9 prefix check: agent-check must name $VAULT"
(cd agent && python3 -m leash_agent check) | tee "$OUT/agent-check.txt" | grep -q "$VAULT" || { echo "agent-check does not name $VAULT: stop"; exit 1; }

step "2/9 accounts, deploy, policy"
make accounts-testnet
make deploy-testnet
make policy-testnet

step "3/9 intents config, routes, function-call keys"
make intents-config YES=1 CONFIRM_VAULT="$VAULT"
make intents-routes YES=1 CONFIRM_VAULT="$VAULT"
make intents-keys YES=1 ASIDE=1

step "4/9 attack suite (signed by the agent)"
make attack-testnet 2>&1 | tee "$OUT/attack.txt"

step "5/9 one approval and one allowed spend_intent"
ROUTE=0 SWAP_AMOUNT=0.1 DEPOSIT="$DEPOSIT" make intents-approve YES=1 2>&1 | tee "$OUT/approve.txt"
ROUTE=0 SWAP_AMOUNT=0.1 DEPOSIT="$DEPOSIT" make intents-spend YES=1 2>&1 | tee "$OUT/spend-allowed.txt"

step "6/9 the same spend again: no approval is left, so it must be denied"
ROUTE=0 SWAP_AMOUNT=0.1 DEPOSIT="$DEPOSIT" make intents-spend YES=1 2>&1 | tee "$OUT/spend-denied.txt" || true

step "7/9 chain check over the agent transactions, in the order they ran"
cat "$OUT/attack.txt" "$OUT/spend-allowed.txt" "$OUT/spend-denied.txt" > "$OUT/agent-transactions.txt"
LEASH_RPC="$LEASH_RPC" scripts/chain-check-testnet.sh "$OUT/agent-transactions.txt" 2>&1 | tee "$OUT/chain-check.txt"

step "8/9 gas per receipt of the two intent transactions"
cat "$OUT/spend-allowed.txt" "$OUT/spend-denied.txt" > "$OUT/intent-transactions.txt"
python3 scripts/tx_gas.py "$LEASH_RPC" "$AGENT" "$OUT/intent-transactions.txt" 2>&1 | tee "$OUT/gas.txt"

step "9/9 status"
make intents-status 2>&1 | tee "$OUT/status.txt"
echo "Files are in $OUT. Expect: chain OK, then chain BROKEN for the altered line."
