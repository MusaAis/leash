#!/usr/bin/env bash
# Replays the decision events of the transactions in LOG against the head the vault reports; then alters one line and expects BROKEN.
# Usage: scripts/chain-check-testnet.sh LOG   (LEASH_RPC overrides the default testnet RPC url)   (LEASH_PREFIX and NETWORK come from the environment as usual)
set -euo pipefail
. "$(dirname "$0")/config.sh"
require_testnet chain-check-testnet
LOG="${1:?usage: chain-check-testnet.sh LOG}"
LEASH_RPC="${LEASH_RPC:-https://test.rpc.fastnear.com}"
EVENTS="$(mktemp)"; TAMPER="$(mktemp)"; trap 'rm -f "$EVENTS" "$TAMPER"' EXIT
OUT="$(python3 "$(dirname "$0")/collect_decisions.py" "$LEASH_RPC" "$VAULT" "$AGENT" "$LOG" "$EVENTS" --head)"
echo "$OUT" | head -1
HEAD="$(echo "$OUT" | sed -n 's/^head //p')"
python3 "$(dirname "$0")/replay_head.py" "$EVENTS" "$HEAD"
sed '1s/"event"/"evenT"/' "$EVENTS" > "$TAMPER"
python3 "$(dirname "$0")/replay_head.py" "$TAMPER" "$HEAD" || true
