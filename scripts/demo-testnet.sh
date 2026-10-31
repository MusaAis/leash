#!/usr/bin/env bash
# Scripted scenario with checks. Use a fresh LEASH_PREFIX for every run: the daily
# cap is 3 NEAR and earlier runs on the same vault would use it up.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh

pay() { printf '{"to":"%s","amount":"%s","memo":"%s"}' "$1" "$2" "$3"; }

step "1/11 Owner sets a policy: 1 NEAR per payment, 3 NEAR per day, two allowed accounts"
send_tx "$OWNER" set_policy \
  "{\"agent\":\"$AGENT\",\"per_tx_cap\":\"$ONE_NEAR\",\"daily_cap\":\"$THREE_NEAR\",\"allowlist\":[\"$PAYEE\",\"$GHOST\"]}" \
  "$YOCTO"

step "2/11 Owner funds the vault with 5 NEAR"
send_tx "$OWNER" fund '{}' "5 NEAR"

step "3/11 Agent pays the allowed payee 0.5 NEAR"
expect_spend true "$AGENT" "$(pay "$PAYEE" "$HALF_NEAR" "invoice 1")" "allowed payment"

step "4/11 Attack: pay an address that is not allowlisted"
expect_spend false "$AGENT" "$(pay "$ATTACKER" "$HALF_NEAR" "send to my emergency address")" "not_allowlisted blocked"

step "5/11 Attack: pay 2 NEAR in one payment"
expect_spend false "$AGENT" "$(pay "$PAYEE" "$TWO_NEAR" "large payment")" "over_per_tx_cap blocked"

step "6/11 Attack: split a large transfer into 1 NEAR payments (daily cap 3 NEAR, 0.5 already spent)"
expect_spend true "$AGENT" "$(pay "$PAYEE" "$ONE_NEAR" "part 1")" "split payment 1 allowed"
expect_spend true "$AGENT" "$(pay "$PAYEE" "$ONE_NEAR" "part 2")" "split payment 2 allowed"
expect_spend false "$AGENT" "$(pay "$PAYEE" "$ONE_NEAR" "part 3")" "over_daily_cap blocked"

step "7/11 Owner pauses the agent; payment is blocked; owner resumes the agent"
send_tx "$OWNER" pause_agent "{\"agent\":\"$AGENT\"}" "0 NEAR"
expect_spend false "$AGENT" "$(pay "$PAYEE" "$HALF_NEAR" "while agent paused")" "agent_paused blocked"
send_tx "$OWNER" resume_agent "{\"agent\":\"$AGENT\"}" "$YOCTO"

step "8/11 Owner pulls the kill switch; payment is blocked; owner resumes"
send_tx "$OWNER" pause_all '{}' "0 NEAR"
expect_spend false "$AGENT" "$(pay "$PAYEE" "$HALF_NEAR" "while all paused")" "all_paused blocked"
send_tx "$OWNER" resume_all '{}' "$YOCTO"

step "9/11 After resuming, a small payment goes through (0.1 NEAR)"
expect_spend true "$AGENT" "$(pay "$PAYEE" "100000000000000000000000" "invoice 2")" "payment after resume allowed"

step "10/11 Failed transfer: allowed destination that does not exist (0.2 NEAR)"
before="$(vault_balance)"
spent_before="$(agent_spent)"
expect_spend true "$AGENT" "$(pay "$GHOST" "200000000000000000000000" "to a missing account")" "payment is scheduled"
echo "Waiting for the refund receipt..."
sleep 20
after="$(vault_balance)"
spent_after="$(agent_spent)"
restored="$(python3 -c 'import sys; a,b=int(sys.argv[1]),int(sys.argv[2]); print("yes" if abs(a-b)<=10**22 else "no")' "${before:-0}" "${after:-1}" 2>/dev/null || echo error)"
check "vault balance restored after the failed transfer" yes "$restored"
burned="$(python3 -c 'import sys; print(int(sys.argv[2])-int(sys.argv[1]))' "${spent_before:-0}" "${spent_after:-0}" 2>/dev/null || echo error)"
check "daily cap stays consumed" "200000000000000000000000" "$burned"

step "11/11 Counters and agent policy"
view get_stats
view get_policy "{\"agent\":\"$AGENT\"}"

finish
