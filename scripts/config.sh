#!/usr/bin/env bash
# Shared settings and helpers for the testnet scripts. Source this file.
# Set LEASH_PREFIX to a unique name (letters, digits, dashes) before first use.

NETWORK="${NETWORK:-testnet}"
PREFIX="${LEASH_PREFIX:-leashdemo}"
VAULT="${PREFIX}-vault.${NETWORK}"
OWNER="${PREFIX}-owner.${NETWORK}"
AGENT="${PREFIX}-agent.${NETWORK}"
PAYEE="${PREFIX}-payee.${NETWORK}"
ATTACKER="${PREFIX}-attacker.${NETWORK}"
GHOST="${PREFIX}-ghost.${NETWORK}"   # intentionally never created
WASM="contract/target/near/leash.wasm"
GAS="30 Tgas"
YOCTO="1 yoctoNEAR"

# Amounts in yoctoNEAR, kept as strings (they exceed 64-bit integers).
HALF_NEAR="500000000000000000000000"
ONE_NEAR="1000000000000000000000000"
TWO_NEAR="2000000000000000000000000"
THREE_NEAR="3000000000000000000000000"

step() {
  printf '\n== %s\n' "$1"
}

# send_tx SIGNER METHOD JSON_ARGS DEPOSIT
send_tx() {
  near contract call-function as-transaction "$VAULT" "$2" json-args "$3" \
    prepaid-gas "$GAS" attached-deposit "$4" \
    sign-as "$1" network-config "$NETWORK" sign-with-legacy-keychain send
}

# view METHOD [JSON_ARGS]
view() {
  local args="${2-}"
  [ -n "$args" ] || args='{}'
  near contract call-function as-read-only "$VAULT" "$1" json-args "$args" \
    network-config "$NETWORK" now
}

PASS=0
FAIL=0

# check LABEL EXPECTED GOT
check() {
  if [ "$3" = "$2" ]; then
    PASS=$((PASS + 1)); echo "PASS  $1 (got $3)"
  else
    FAIL=$((FAIL + 1)); echo "FAIL  $1 (expected $2, got ${3:-nothing})"
  fi
}

# last_bool and first_number read the CLI output of OUR OWN scripted calls, where every memo is fixed text.
# Never use them on output that can contain text from a user, a model or an attacker (a memo could fake a result).
last_bool() { grep -Eow 'true|false' | tail -1 || true; }
first_number() { grep -Eo '[0-9]{6,}' | head -1 || true; }

# expect_spend EXPECTED SIGNER JSON_ARGS LABEL
expect_spend() {
  local out got
  out="$(send_tx "$2" spend "$3" "0 NEAR")"
  [ -z "$out" ] || printf '%s\n' "$out"
  got="$(printf '%s' "$out" | last_bool)"
  check "$4" "$1" "$got"
}

vault_balance() { view get_available_balance 2>/dev/null | first_number; }

agent_spent() {
  view get_policy "{\"agent\":\"$AGENT\"}" 2>/dev/null \
    | grep -Eo '"spent_in_window": *"[0-9]+"' | first_number
}

finish() {
  echo
  echo "Result: $PASS passed, $FAIL failed"
  [ "$FAIL" -eq 0 ]
}
