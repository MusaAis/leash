#!/usr/bin/env bash
# Creates the testnet accounts used by the demo, funded by the testnet faucet.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh

for id in "$VAULT" "$OWNER" "$AGENT" "$PAYEE"; do
  echo "Creating $id"
  near account create-account sponsor-by-faucet-service "$id" \
    autogenerate-new-keypair save-to-legacy-keychain \
    network-config "$NETWORK" create
done

echo "Done. Next: make deploy-testnet"
