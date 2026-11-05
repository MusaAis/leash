#!/usr/bin/env bash
# Deploys the built wasm to the vault account and initializes it in one transaction.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh
require_testnet deploy-testnet

[ -f "$WASM" ] || { echo "Missing $WASM. Run: make build"; exit 1; }

echo "Deploying $VAULT with owner $OWNER"
near contract deploy "$VAULT" use-file "$WASM" \
  with-init-call new json-args "{\"owner\": \"$OWNER\"}" \
  prepaid-gas "$GAS" attached-deposit '0 NEAR' \
  network-config "$NETWORK" sign-with-legacy-keychain send

step "Owner stored on-chain"
view get_owner
echo "Next: make demo-testnet"
