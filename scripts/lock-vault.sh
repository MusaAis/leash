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
if [ "$NETWORK" != "testnet" ] && [ "${LEASH_ALLOW_MAINNET:-}" != "1" ]; then
  echo "lock-vault outside testnet needs LEASH_ALLOW_MAINNET=1 for this one command (NETWORK=$NETWORK)." >&2
  exit 1
fi

# Key lists come from the RPC as JSON (scripts/full_access_keys.py), not from CLI text.
# Exit 2 from the helper means "could not verify": the script then stops, it never assumes success.
keys_now() {
  local out rc=0
  out="$(python3 scripts/full_access_keys.py "$VAULT" "$NETWORK")" || rc=$?
  [ "$rc" -le 1 ] || { echo "Could not read the key list from the RPC. Nothing was changed." >&2; exit 1; }
  printf '%s\n' "$out"
}

echo "Full-access keys on $VAULT:"
before="$(keys_now)"
printf '%s\n' "$before"

read -r -p "Public key to delete (ed25519:...): " key
printf '%s\n' "$before" | grep -Fxq "FULL_ACCESS $key" \
  || { echo "That key is not a full-access key of $VAULT. Nothing was changed."; exit 1; }
read -r -p "Type the vault account name to confirm: " confirm
[ "$confirm" = "$VAULT" ] || { echo "Cancelled."; exit 1; }

near account delete-keys "$VAULT" public-keys "$key" \
  network-config "$NETWORK" sign-with-legacy-keychain send

echo "Full-access keys after deletion (the RPC can lag a few blocks):"
for attempt in 1 2 3 4 5; do
  after="$(keys_now)"
  printf '%s\n' "$after" | grep -Fxq "COUNT 0" && break
  [ "$attempt" -lt 5 ] && sleep "${LEASH_LOCK_WAIT:-3}"
done
printf '%s\n' "$after"
printf '%s\n' "$after" | grep -Fxq "COUNT 0" || { echo "The vault still has a full-access key."; exit 1; }
echo "Verified: no full-access key remains on $VAULT."
