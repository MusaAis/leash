#!/usr/bin/env bash
# Replays the decision events of the transactions in LOG against the head the vault reports; then alters one line and expects BROKEN.
# Usage: LEASH_RPC=<testnet rpc url> scripts/chain-check-testnet.sh LOG   (LEASH_PREFIX and NETWORK come from the environment as usual)
set -euo pipefail
. "$(dirname "$0")/config.sh"
require_testnet chain-check-testnet
LOG="${1:?usage: chain-check-testnet.sh LOG}"
: "${LEASH_RPC:?set LEASH_RPC to a testnet RPC url}"
EVENTS="$(mktemp)"; TAMPER="$(mktemp)"; trap 'rm -f "$EVENTS" "$TAMPER"' EXIT
OUT="$(python3 "$(dirname "$0")/collect_decisions.py" "$LEASH_RPC" "$VAULT" "$AGENT" "$LOG" "$EVENTS" --head)"
echo "$OUT" | head -1
HEAD="$(echo "$OUT" | sed -n 's/^head //p')"
python3 "$(dirname "$0")/replay_head.py" "$EVENTS" "$HEAD"
sed '1s/"event"/"evenT"/' "$EVENTS" > "$TAMPER"
python3 "$(dirname "$0")/replay_head.py" "$TAMPER" "$HEAD" || true
