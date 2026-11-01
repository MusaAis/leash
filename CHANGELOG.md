# Changelog

All notable changes are listed here. Versions follow semantic versioning.

## [Unreleased]
### Added
- `make intents-wet-probe`: one real 1Click quote (at most 0.15 NEAR, default 0.1; `PROBE_AMOUNT=0.15` to change it) to read the deposit address format. Nothing is deposited. Not yet run against the live service.
- `make wrap-storage`: read-only view calls on `wrap.near` (`storage_balance_bounds`, `storage_balance_of`). No transaction, no key. First live read: the storage deposit is 0.00125 NEAR (min equals max).
- `make agent-signature`: asks the configured model and `z-ai/glm-5.3` one question and reports which kind of signature the gateway gives. The signature itself is not verified. Not yet run live.

### Fixes from the review of v0.3.0
- Error and rejection messages no longer carry the recipient or the refund account, and everything printed hides both in any letter case.
- Every call to 1Click counts toward the limits (10 a minute, 60 an hour), also rejected and failed ones; a lock file covers the check and the entry.
- An existing guardian log with wider permissions is set to 0600 on the next write.
- `.env`: keys starting with `LEASH_ALLOW_` are refused in the file, and `export KEY=value` lines count like `KEY=value`.
- Answers from NEAR AI Cloud are read up to 2 MB.
- `scripts/check_docs.py` accepts a `-fixN` suffix on a version.
- From the first real quote: the quote's deadline may be up to 4 days after the requested one (the live service gave 3 days), and the fee entry may carry `limitOrderId: null`. The open-quote rule uses the requested deadline.
- `make agent-signature MODELS="a b"` chooses the models.
- Docs: the Chain Signatures row is marked roadmap, the refund account text matches the real flow, an unverified fee remark is removed, and "checked against the live API" no longer reads as "deployed on mainnet".
- Tests: 198 agent tests (161 before).

## [0.3.0] - 2026-11-01
### Added
- NEAR Intents (1Click) dry quotes: `agent/leash_agent/intents.py` (client, request builder, answer check) and `guardian.py` (route id and amount in, checked quote out, private log, rate limit). `make intents-tokens` and `make intents-dry`. No funds move. See `docs/intents.md`.
- Live dry quotes on mainnet at nine sizes from 0.5 to 0.001 NEAR, recorded in `docs/intents.md`.
- Tests: 161 agent tests (127 before).
### Security
- A repeated key in `.env` is an error that names the key and the lines, never a value. A value already set in the environment still wins.
- The guardian accepts one recorded 1Click fee entry (20 bps) and rejects every other fee, recipient, refund or asset change in a quote answer.
### Changed
- Mainnet account names end in `.near` (was `.mainnet`).
- `docs/security.md` and the README no longer announce an ownership transfer: there is none.
### Fixed
- Requests to 1Click use a named user agent. The default Python agent is blocked (HTTP 403, Cloudflare 1010).

## [0.2.3] - 2026-11-01
### Security
- An attack run first reads the vault (policy, pauses, allowlist, room under the caps, balance) and stops with exit code 3, sending nothing, if the run could prove nothing. Every vault answer is compared with what the policy predicts; a difference marks the run as invalid. (A-004.)
- Payment amounts are compared. In the live scenario without an instruction any payment is unauthorized; with an invoice the payment must be exactly the invoice. The report counts `agent fooled` and `funds lost` separately, and the control of the invoice scenario comes from its own rows. Any unauthorized payment exits with code 2. (A-005.)
- Attack text is escaped before it goes into the `<untrusted>` block, so it cannot close the block. New attacks: a fake closing tag and a fake owner approval (ten in total). `SAMPLES=n` asks the model n times per attack, `LEASH_TEMPERATURE` sets the sampling temperature. (A-010.)
- Long input is refused without a model call. Model calls and tokens are also capped per UTC day in `~/.leash/budget.json`. The API key is only sent to `https://cloud-api.near.ai`, and redirects are refused. (A-011.)
- A vault answer without its event is `UNKNOWN`, no longer counted as blocked or paid, and makes the run invalid. (A-012.)
- `make lock-vault-testnet` reads the key list from the RPC as JSON, refuses a key that is not a full-access key of the vault, and fails unless no full-access key remains afterwards. (A-008.)
### Added
- `scripts/check_docs.py`, run by `make check` and CI: compares the docs with the git tags, the test counts, the denial reasons, the attack count, the cost-limit defaults, the make targets and the relative links.
- `docs/testnet-proof.md` Runs 5 to 7: hijacked agent, live model at temperature 0 and with three samples at temperature 0.7, and a vault lock rehearsal, with a link for every vault call.
- Tests: 127 agent tests (67 before), including a real local server for the redirect check and an end-to-end test of the lock script against a fake RPC.
### Changed
- The authorized invoice in the live scenario is 0.2 NEAR (was 0.3), so ten invoices fit under the 3 NEAR daily cap. A hijacked run on a fresh policy now shows 11 blocked and 3 paid to the payee by design.
- A live run makes 21 model calls (61 with three samples).
### Fixed
- `docs/STATE.md` called the released v0.2.2 a candidate.
- Removed an unused helper and the cap warning that the preflight replaces.

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
