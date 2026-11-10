#!/usr/bin/env bash
# Writes the two service files for the Arena and its tunnel into a folder; nothing is installed.
# Usage: scripts/arena-units.sh <folder>
# The tunnel is a quick tunnel: it needs no account and no domain, and it gets a new address when it restarts.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:?usage: scripts/arena-units.sh <folder>}"
ROOT="$(pwd)"
RUN_USER="$(id -un)"
HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"
TUNNEL="$(command -v cloudflared || true)"
[ -n "$TUNNEL" ] || { echo "cloudflared is not installed." >&2; exit 1; }
mkdir -p "$OUT"
cat > "$OUT/leash-arena.service" <<UNIT
[Unit]
Description=LEASH Arena (testnet)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$ROOT/agent
EnvironmentFile=$HOME_DIR/.leash/arena.env
ExecStart=/usr/bin/python3 -m leash_arena --live
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
MemoryMax=300M

[Install]
WantedBy=multi-user.target
UNIT
cat > "$OUT/leash-arena-tunnel.service" <<UNIT
[Unit]
Description=LEASH Arena quick tunnel
After=leash-arena.service
Requires=leash-arena.service

[Service]
User=$RUN_USER
ExecStart=$TUNNEL tunnel --no-autoupdate --config /dev/null --url http://127.0.0.1:8081
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
MemoryMax=150M

[Install]
WantedBy=multi-user.target
UNIT
echo "wrote $OUT/leash-arena.service and $OUT/leash-arena-tunnel.service"
