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
