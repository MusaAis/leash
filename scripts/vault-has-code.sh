#!/usr/bin/env bash
# Usage: scripts/vault-has-code.sh <account> <sha256 hex of the wasm> [tries]
# Exit 0 when the account's deployed code hash equals the given sha256 (read as hex or as base58).
# Reads the account summary up to <tries> times (default 6), LEASH_CODE_WAIT seconds apart (default 5).
set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/config.sh
ACCOUNT="${1:?usage: vault-has-code.sh <account> <sha256> [tries]}"
SHA="${2:?usage: vault-has-code.sh <account> <sha256> [tries]}"
TRIES="${3:-6}"
B58="$(python3 - "$SHA" <<'PY'
import sys
A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
raw = sys.argv[1]
if len(raw) != 64:
    raise SystemExit(1)
data = bytes.fromhex(raw)
n, out = int.from_bytes(data, "big"), ""
while n:
    n, r = divmod(n, 58)
    out = A[r] + out
print("1" * (len(data) - len(data.lstrip(b"\0"))) + out)
PY
)" || { echo "the sha256 must be 64 hex characters" >&2; exit 2; }
for i in $(seq 1 "$TRIES"); do
  SUMMARY="$(near account view-account-summary "$ACCOUNT" network-config "$NETWORK" now 2>&1)"
  if printf '%s' "$SUMMARY" | grep -qiF -e "$SHA" || printf '%s' "$SUMMARY" | grep -qwF -e "$B58"; then
    echo "code on $ACCOUNT matches $SHA"
    exit 0
  fi
  [ "$i" -lt "$TRIES" ] && sleep "${LEASH_CODE_WAIT:-5}"
done
echo "code on $ACCOUNT does not match $SHA. Last lines that mention the code:" >&2
printf '%s\n' "$SUMMARY" | grep -iE 'hash|sha|contract|code' | head -5 >&2
exit 1
