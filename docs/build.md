# Build and artifact

How to build the vault contract and check the result against the deployed code.

## Toolchain

| Input | Value |
|---|---|
| Rust | 1.96.0, pinned in `contract/rust-toolchain.toml` |
| `near-sdk` | `=5.28.3`, pinned in `contract/Cargo.toml` and `contract/Cargo.lock` |
| `cargo-near` | 0.22.0 (recorded; not enforced) |

The mainnet runtime ran protocol version 86 when the vault was deployed. The `near-sdk` pin keeps the wasm compatible with it.

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
| Vault deployed on mainnet | 2026-11-04 |
| Builds compared | 2026-11-04 and 2026-11-05 |

The base58 code hash is the same value as the sha256 above, encoded in base58. The mainnet deploy of `leash-vault.near` printed this code hash.

Building the same source with the pinned toolchain gave this sha256 in two builds on separate days. `make build-info` prints the value to compare.

## Artifact of `v0.3.2`

| Fact | Value |
|---|---|
| Size | 198,968 bytes |
| sha256 | `18f112959fc2a6ec4a83489803589c83872c43564e96cd94ac70df1f1113dff0` |
| Code hash on chain (base58) | `2gMzTHTr2TwtwAaHsXZbKH9nBLi2ZfU9HLMVHG82B8KZ` |
| Host imports | 36, all in the host function tables of nearcore 2.13.1 and 2.14.0, including `sha256` |
| Mainnet protocol version read | 88 (2026-11-08) |

Deploy this file by its sha256, and read the code hash from the chain after the deploy (`scripts/vault-has-code.sh`). A rebuild of the same source with another `cargo-near` version or on another CPU architecture can give a different file: one rebuild gave 198,987 bytes with sha256 `dde87677dbd50cc8ee04b3a4f9ee27c6ec2efe5b60ba6143138dd53450214afb`.

## Import list

`scripts/wasm_imports.py` lists the host functions the wasm imports. Compare the list with the host functions the target network supports before any deploy.
