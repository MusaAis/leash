# Changelog

All notable changes are listed here. Versions follow semantic versioning.

## [0.1.2] - 2026-10-31
### Added
- Scripted testnet deployment and demo scenario (`make accounts-testnet`, `make deploy-testnet`, `make demo-testnet`).
- Script to remove full-access keys from the vault account.
- Deployment guide.
- Tests for the one-yoctoNEAR rule on every fund-moving owner method, and for the fund and withdraw paths.
### Changed
- `make lint` now includes the format check, matching CI.

## [0.1.1] - 2026-10-31
### Changed
- Owner methods that move funds or widen limits require exactly 1 yoctoNEAR.
- Initialization is restricted to the contract account.
- Callers without a policy are reverted and no longer affect counters.
- Over-long memos are denied with `memo_too_long`.
- `get_policy` reports the effective spent amount for the current window.
### Added
- Security model, architecture decision records, CI workflow, release script.

## [0.1.0] - 2026-10-31
### Added
- Vault contract with per-transaction cap, daily cap, destination allowlist, per-agent pause and global kill switch.

## [0.0.1] - 2026-10-31
### Added
- Repository structure and MIT license.
