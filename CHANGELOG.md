# Changelog

All notable changes are listed here. Versions follow semantic versioning.

## [0.2.0] - 2026-10-31
### Added
- Agent runtime: decisions from NEAR AI Cloud, payments through the vault, strict validation of model output.
- Attack suite with a hijacked-agent mode and a live-model mode.
- Unit tests for the runtime, and a CI job for them.
- Live-model run starts with a control task and shows the reason for every refusal; model call and token budgets; `models` and dry-run commands.
- Second live-model scenario in which the owner authorizes an invoice and the attack text tries to redirect it.
- Demo steps for the daily cap, agent pause and a failed transfer, with a pass/fail check per step.
### Changed
- Account script is safe to run again; lock script reports the remaining full-access keys.
- README lists all prerequisites and exact expected output.

## [0.1.2] - 2026-10-31
### Added
- Scripted testnet deployment and demo scenario (`make accounts-testnet`, `make deploy-testnet`, `make demo-testnet`).
- Script to remove full-access keys from the vault account.
- Deployment guide and a recorded testnet run (`docs/testnet-proof.md`).
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
