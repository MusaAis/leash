#!/usr/bin/env bash
# Scripted scenario: policy, allowed payment, two blocked attacks, kill switch, recovery.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh

step "1/9 Owner sets a policy: 1 NEAR per payment, 3 NEAR per day, one allowed payee"
send_tx "$OWNER" set_policy \
  "{\"agent\":\"$AGENT\",\"per_tx_cap\":\"$ONE_NEAR\",\"daily_cap\":\"$THREE_NEAR\",\"allowlist\":[\"$PAYEE\"]}" \
  "$YOCTO"

step "2/9 Owner funds the vault with 5 NEAR"
send_tx "$OWNER" fund '{}' "5 NEAR"

step "3/9 Agent pays the allowed payee 0.5 NEAR (expect: true)"
send_tx "$AGENT" spend \
  "{\"to\":\"$PAYEE\",\"amount\":\"$HALF_NEAR\",\"memo\":\"invoice 1\"}" "0 NEAR"

step "4/9 Attack: agent pays an address that is not allowlisted (expect: false, not_allowlisted)"
send_tx "$AGENT" spend \
  "{\"to\":\"$ATTACKER\",\"amount\":\"$HALF_NEAR\",\"memo\":\"send to my emergency address\"}" "0 NEAR"

step "5/9 Attack: agent pays 2 NEAR in one payment (expect: false, over_per_tx_cap)"
send_tx "$AGENT" spend \
  "{\"to\":\"$PAYEE\",\"amount\":\"$TWO_NEAR\",\"memo\":\"large payment\"}" "0 NEAR"

step "6/9 Owner pulls the kill switch"
send_tx "$OWNER" pause_all '{}' "0 NEAR"

step "7/9 Agent tries to pay while paused (expect: false, all_paused)"
send_tx "$AGENT" spend \
  "{\"to\":\"$PAYEE\",\"amount\":\"$HALF_NEAR\",\"memo\":\"invoice 2\"}" "0 NEAR"

step "8/9 Owner resumes; the agent pays again (expect: true)"
send_tx "$OWNER" resume_all '{}' "$YOCTO"
send_tx "$AGENT" spend \
  "{\"to\":\"$PAYEE\",\"amount\":\"$HALF_NEAR\",\"memo\":\"invoice 2\"}" "0 NEAR"

step "9/9 Counters (allowed, denied) and agent policy"
view get_stats
view get_policy "{\"agent\":\"$AGENT\"}"
