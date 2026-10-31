#!/usr/bin/env bash
# Creates the testnet accounts used by the demo, funded by the testnet faucet.
# Safe to run again: accounts that already exist are skipped.
# The ghost account is never created on purpose (see demo step 10).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh

for id in "$VAULT" "$OWNER" "$AGENT" "$PAYEE" "$ATTACKER"; do
  if near account view-account-summary "$id" network-config "$NETWORK" now >/dev/null 2>&1; then
    echo "Exists, skipped: $id"
    continue
  fi
  echo "Creating $id"
  near account create-account sponsor-by-faucet-service "$id" \
    autogenerate-new-keypair save-to-legacy-keychain \
    network-config "$NETWORK" create
done

echo "Done. Next: make deploy-testnet"
