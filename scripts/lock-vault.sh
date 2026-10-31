#!/usr/bin/env bash
# Deletes a full-access key from the vault account. This is irreversible.
# Lock only the FINAL vault (for example the mainnet one). A locked contract can
# no longer be upgraded or replaced. Rehearse on a separate prefix, and do not
# lock a vault you still want to redeploy.
# The owner can still call withdraw after locking: locking removes upgrades,
# not the owner's exit.
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
keys="$(near account list-keys "$VAULT" network-config "$NETWORK" now 2>&1)"
printf '%s\n' "$keys"
remaining="$(printf '%s\n' "$keys" | grep -c 'FullAccess' || true)"
echo "Full-access keys remaining: $remaining"
[ "$remaining" -eq 0 ] || { echo "The vault still has a full-access key."; exit 1; }
