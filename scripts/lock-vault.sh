#!/usr/bin/env bash
# Deletes a full-access key from the vault account. This is irreversible:
# after the last key is removed, the contract can no longer be upgraded or replaced.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh

echo "Keys on $VAULT:"
near account list-keys "$VAULT" network-config "$NETWORK" now

read -r -p "Public key to delete (ed25519:...): " key
read -r -p "Type the vault account name to confirm: " confirm
[ "$confirm" = "$VAULT" ] || { echo "Cancelled."; exit 1; }

near account delete-keys "$VAULT" public-keys "$key" \
  network-config "$NETWORK" sign-with-legacy-keychain send

echo "Keys after deletion:"
near account list-keys "$VAULT" network-config "$NETWORK" now
