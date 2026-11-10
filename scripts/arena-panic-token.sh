#!/bin/sh
# Make a panic token: print it once; only its SHA-256 goes into the settings file.
set -eu
FILE="${LEASH_ARENA_ENV:-$HOME/.leash/arena.env}"
mkdir -p "$(dirname "$FILE")"
if [ -f "$FILE" ] && grep -q '^LEASH_ARENA_PANIC_SHA256=' "$FILE"; then
  echo "A panic token hash is already set. Remove that line to make a new token." >&2
  exit 1
fi
TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
HASH="$(printf %s "$TOKEN" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
umask 077
printf 'LEASH_ARENA_PANIC_SHA256=%s\n' "$HASH" >> "$FILE"
chmod 600 "$FILE"
echo "Token (shown once; keep it in a password manager, never in a chat):"
echo "$TOKEN"
