#!/usr/bin/env bash
# Sets the agent policy and funds the vault, without running the full demo.
# Use it on a test vault before the attack suites:
#   1 NEAR per payment, 3 NEAR per day, one allowed payee.
# It removes any earlier policy first, so the agent's daily counter starts at zero.
# That makes an old test vault reusable. Do not use it on a vault with real funds.
# Funding: LEASH_FUND="2 NEAR" sets the amount (default 5 NEAR); LEASH_FUND=0 skips funding.
# Optional: LEASH_PER_TX_YOCTO and LEASH_DAILY_YOCTO set the caps; LEASH_ALLOW_EXTRA adds one account to the allowlist.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh
require_testnet policy-testnet
FUND="${LEASH_FUND:-5 NEAR}"
PER_TX="${LEASH_PER_TX_YOCTO:-$ONE_NEAR}"
DAILY="${LEASH_DAILY_YOCTO:-$THREE_NEAR}"
EXTRA="${LEASH_ALLOW_EXTRA:-}"
case "$PER_TX$DAILY" in *[!0-9]*|"") echo "the caps must be whole numbers of yoctoNEAR." >&2; exit 1 ;; esac
case "$EXTRA" in *[!a-z0-9._-]*) echo "LEASH_ALLOW_EXTRA is not an account id." >&2; exit 1 ;; esac
ALLOW="\"$PAYEE\""
[ -z "$EXTRA" ] || ALLOW="$ALLOW,\"$EXTRA\""

step "Owner clears any earlier policy (fresh daily counter)"
send_tx "$OWNER" remove_agent "{\"agent\":\"$AGENT\"}" "$YOCTO"

step "Owner sets the policy"
send_tx "$OWNER" set_policy \
  "{\"agent\":\"$AGENT\",\"per_tx_cap\":\"$PER_TX\",\"daily_cap\":\"$DAILY\",\"allowlist\":[$ALLOW]}" \
  "$YOCTO"

if [ "$FUND" != "0" ]; then
  step "Owner funds the vault with $FUND"
  send_tx "$OWNER" fund '{}' "$FUND"
fi

step "Policy in place"
view get_policy "{\"agent\":\"$AGENT\"}"
echo "Next: make attack-llm-testnet, then make attack-testnet"
