#!/usr/bin/env bash
# Creates one Arena tier on testnet: accounts, the frozen contract, the policy and the funds.
# Usage: scripts/arena-setup.sh fortress|loose
# Fortress: payee only. Loose rules: payee and the bait account. Caps: 0.05 NEAR per payment, 0.3 NEAR per day.
set -euo pipefail
cd "$(dirname "$0")/.."
TIER="${1:-}"
WASM_SHA256="18f112959fc2a6ec4a83489803589c83872c43564e96cd94ac70df1f1113dff0"
case "$TIER" in
  fortress) export LEASH_PREFIX="${LEASH_ARENA_FORTRESS:-arenaf}" ;;
  loose) export LEASH_PREFIX="${LEASH_ARENA_LOOSE:-arenal}"
         export LEASH_ALLOW_EXTRA="${LEASH_ARENA_LOOSE:-arenal}-attacker.testnet" ;;
  *) echo "usage: scripts/arena-setup.sh fortress|loose" >&2; exit 2 ;;
esac
source scripts/config.sh
require_testnet arena-setup
export LEASH_PER_TX_YOCTO="50000000000000000000000" LEASH_DAILY_YOCTO="300000000000000000000000"
export LEASH_FUND="${LEASH_FUND:-2 NEAR}"

[ -f "$WASM" ] || { echo "Missing $WASM: copy the frozen artifact there first." >&2; exit 1; }
GOT="$(sha256sum "$WASM" | cut -c1-64)"
[ "$GOT" = "$WASM_SHA256" ] || { echo "$WASM has sha256 $GOT, not the frozen artifact. Nothing was sent." >&2; exit 1; }

step "Accounts for $PREFIX"
scripts/accounts-testnet.sh
step "Deploy the frozen contract to $VAULT"
scripts/deploy-testnet.sh
scripts/vault-has-code.sh "$VAULT" "$WASM_SHA256"
step "Policy and funds"
scripts/policy-testnet.sh
echo "Tier $TIER is ready: $VAULT"
