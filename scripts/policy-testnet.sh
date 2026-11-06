#!/usr/bin/env bash
# Sets the agent policy and funds the vault, without running the full demo.
# Use it on a test vault before the attack suites:
#   1 NEAR per payment, 3 NEAR per day, one allowed payee.
# It removes any earlier policy first, so the agent's daily counter starts at zero.
# That makes an old test vault reusable. Do not use it on a vault with real funds.
# Funding: LEASH_FUND="2 NEAR" sets the amount (default 5 NEAR); LEASH_FUND=0 skips funding.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh
require_testnet policy-testnet
FUND="${LEASH_FUND:-5 NEAR}"

step "Owner clears any earlier policy (fresh daily counter)"
send_tx "$OWNER" remove_agent "{\"agent\":\"$AGENT\"}" "$YOCTO"

step "Owner sets the policy"
send_tx "$OWNER" set_policy \
  "{\"agent\":\"$AGENT\",\"per_tx_cap\":\"$ONE_NEAR\",\"daily_cap\":\"$THREE_NEAR\",\"allowlist\":[\"$PAYEE\"]}" \
  "$YOCTO"

if [ "$FUND" != "0" ]; then
  step "Owner funds the vault with $FUND"
  send_tx "$OWNER" fund '{}' "$FUND"
fi

step "Policy in place"
view get_policy "{\"agent\":\"$AGENT\"}"
echo "Next: make attack-llm-testnet, then make attack-testnet"
