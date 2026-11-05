#!/usr/bin/env bash
# Prints what identifies a contract build: toolchain, SDK version, size, sha256, import count.
set -euo pipefail
cd "$(dirname "$0")/.."
WASM="contract/target/near/leash.wasm"
[ -f "$WASM" ] || { echo "Missing $WASM. Run: make build" >&2; exit 1; }
echo "rustc:      $(rustc --version)"
echo "cargo-near: $(cargo near --version 2>&1 | head -1)"
echo "near-sdk:   $(awk '/^name = "near-sdk"$/ {getline; print $3; exit}' contract/Cargo.lock | tr -d '"')"
echo "size:       $(wc -c < "$WASM") bytes"
echo "sha256:     $(sha256sum "$WASM" | cut -c1-64)"
echo "imports:    $(python3 scripts/wasm_imports.py "$WASM" | head -1)"
