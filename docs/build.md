# Build and artifact

How to build the vault contract and check the result against the deployed code.

## Toolchain

| Input | Value |
|---|---|
| Rust | 1.96.0, pinned in `contract/rust-toolchain.toml` |
| `near-sdk` | `=5.28.3`, pinned in `contract/Cargo.toml` and `contract/Cargo.lock` |
| `cargo-near` | 0.22.0 (recorded; not enforced) |

The mainnet runtime ran protocol version 86 when the vault was deployed on 2026-11-04. The `near-sdk` pin keeps the wasm compatible with it.

## Build and check

- `make build` builds the wasm artifact.
- `make build-info` prints the toolchain versions, the artifact size, its sha256 and the number of host imports.

## Artifact of `v0.3.1`

| Fact | Value |
|---|---|
| Size | 189,825 bytes |
| sha256 | `dc3095a72ed04e2c1dddedaeae5e25044d22ca0fe8426e93643efa01d86858f9` |
| Code hash on chain (base58) | `FpXfPq1ejnsvDVAV3LdRD4RAkGVEPfSAmxecYHPUMq1i` |
| Host imports | 35 |

The base58 code hash is the same value as the sha256 above, encoded in base58. The mainnet deploy of `leash-vault.near` printed this code hash.

Building the same source with the pinned toolchain gave this sha256 on 2026-11-04 and again on 2026-11-05. `make build-info` prints the value to compare.

## Import list

`scripts/wasm_imports.py` lists the host functions the wasm imports. Compare the list with the host functions the target network supports before any deploy.
