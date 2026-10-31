# Changelog

All notable changes are listed here. Versions follow semantic versioning.

## [0.2.2] - 2026-10-31
### Security
- The model's answer is read strictly: exactly one JSON object with known keys. The chain-of-thought text (`reasoning_content`) is never used, a cut-off reply is never parsed, and text around the object, two objects or an unclosed `<think>` block are refused. (Review finding A-001.)
- The transaction id is read only from the CLI's own `Transaction ID:` line, and event lines only when they start with `EVENT_JSON:`, so text inside a memo cannot fake an id or an event. Memos are cleaned of control characters and cut to 100 bytes. (A-006, B-001, A-009.)
- Amounts must be plain decimals; `1e999999999` and similar no longer crash the run. Account ids are matched in full. `Vault.spend` validates the destination and memo itself. (A-002, A-003.)
- The pay commands refuse a network other than testnet unless `LEASH_ALLOW_NON_TESTNET=1`; `make policy-testnet` is testnet only. (A-013.)
### Added
- Every attack report row carries the transaction id of its vault call, in the printed report and in the `--json` file, so a run can be linked to the explorer row by row.
- `make agent-models` says when the configured model is missing from the list (its price is then not shown), or when the list is empty.
- A vault call without a valid transaction id is printed as `tx MISSING` and the run exits with code 3. (B-002.)
- Tests for `main` (exit codes 0, 1, 2 and 3, the JSON file, the budget line, the models note), the strict parser, hostile memos and the `.env` loader. (B-004.)
- `docs/testnet-proof.md` Run 4: a live-model and hijacked-agent run with a transaction link for every vault call.
- `docs/architecture.md`: components, accounts, the order of the vault checks, events.
### Changed
- README restructured: verified results table, how LEASH uses NEAR, project structure and a disclosure section. Test counts and results match the testnet proof.
- Diagrams are now SVG files (`docs/img/`) instead of Mermaid: the GitHub mobile app shows Mermaid as raw code, and the new diagrams stay readable on a phone.
### Fixed
- `make accounts-testnet` stops when it cannot tell whether an account exists, instead of moving key files of an account that does. It lists the files it moved aside. `LEASH_FORCE_CREATE=1` skips the check. (A-007.)
- The `.env` loader no longer keeps an inline comment as part of a value; `.gitignore` covers `.env.*`, `*.pem` and saved reports. (A-016.)
- Docs: the amount of the authorized payments was not compared, refused attempts have no transaction id, the owner's counter reset is documented, and the NEAR Builders credits sentence is replaced by the instructions link. (B-003, B-005, A-014.)
- `docs/STATE.md` and the testnet proof still called v0.2.1 a candidate after its release.
- README listed the denial reasons in an order that did not match the contract.

## [0.2.1] - 2026-10-31
### Added
- `make policy-testnet` to prepare a vault for the attack suites; the live-model run warns when the daily cap is too low to test anything.
### Changed
- Default per-call token limit raised to 4000: the model reasons before it answers, and 1500 cut off the control task.
- Run-wide limit on model tokens (`LEASH_MAX_TOTAL_TOKENS`, default 80000); the run reports tokens used.
- `make policy-testnet` clears the earlier policy first, so a test vault can be reused with a fresh daily counter; `LEASH_FUND` sets the funding amount.
- `make accounts-testnet` retries when the testnet faucet is rate limited, and moves aside key files left by a failed attempt.

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
