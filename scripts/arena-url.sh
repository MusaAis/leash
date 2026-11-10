#!/usr/bin/env bash
# Prints the public address of the Arena tunnel, read from the tunnel's log.
set -euo pipefail
URL="$( (journalctl -u leash-arena-tunnel -n 300 --no-pager 2>/dev/null || sudo journalctl -u leash-arena-tunnel -n 300 --no-pager) | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
[ -n "$URL" ] || { echo "no tunnel address in the log yet." >&2; exit 1; }
echo "$URL"
