#!/usr/bin/env bash
# Creates the testnet accounts used by the demo, funded by the testnet faucet.
# Safe to run again: accounts that already exist are skipped.
# Retries when the faucet is rate limited, and moves aside key files left by a failed attempt.
# The ghost account is never created on purpose (see demo step 10).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh

for id in "$VAULT" "$OWNER" "$AGENT" "$PAYEE" "$ATTACKER"; do
  if near account view-account-summary "$id" network-config "$NETWORK" now >/dev/null 2>&1; then
    echo "Exists, skipped: $id"
    continue
  fi
  # The faucet answers 429 when it is busy. Wait and retry before giving up.
  created=no
  for attempt in 1 2 3 4 5; do
    # A failed creation leaves a key file behind, and the CLI will not overwrite it, so the
    # new key would never be saved. Move such files aside (nothing is deleted).
    cred_dir="${HOME}/.near-credentials/${NETWORK}"
    for stale in "$cred_dir/$id.json" "$cred_dir/$id"; do
      if [ -e "$stale" ]; then
        mv "$stale" "$stale.stale-$(date +%s)"
        echo "Moved aside a stale key file: $stale"
      fi
    done
    echo "Creating $id (attempt $attempt of 5)"
    if near account create-account sponsor-by-faucet-service "$id" \
        autogenerate-new-keypair save-to-legacy-keychain \
        network-config "$NETWORK" create; then
      created=yes
      break
    fi
    wait=$((attempt * 30))
    echo "Faucet failed. Waiting ${wait}s before retrying."
    sleep "$wait"
  done
  [ "$created" = yes ] || { echo "Could not create $id. Run make accounts-testnet again later."; exit 1; }
done

echo "Done. Next: make deploy-testnet"
