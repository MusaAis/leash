# Changelog

All notable changes are listed here. Versions follow semantic versioning.

## [Unreleased]

## [0.3.1] - 2026-11-04
### Added
- Mainnet: the vault is deployed at `leash-vault.near` and one swap of 0.1 NEAR to USDC on Base went through it on 2026-11-04. Transactions, measured gas (9.853 Tgas for the whole path) and balances are in `docs/mainnet-proof.md`.
- The contract is built with `near-sdk` 5.28.3 (`contract/Cargo.lock` only; no source change) because mainnet's runtime (protocol version 86 at the time) rejected the build made with `near-sdk` 5.30.0 with a link error; the build made with 5.28.3 deployed.
- `make intents-wet-probe`: one real 1Click quote (at most 0.15 NEAR, default 0.1; `PROBE_AMOUNT=0.15` to change it) to read the deposit address format. Nothing is deposited. Run live twice on 2026-11-01 (two wet quotes, nothing deposited); the probe stops after 3 wet quotes in the log.
- `make wrap-storage`: read-only view calls on `wrap.near` (`storage_balance_bounds`, `storage_balance_of`). No transaction, no key. First live read: the storage deposit is 0.00125 NEAR (min equals max).
- `make agent-signature`: asks the configured model and `z-ai/glm-5.3` one question and reports which kind of signature the gateway gives. The signature itself is not verified. Run live: `z-ai/glm-5.3` signs as `gateway`, `z-ai/glm-5.3-flash` reports `provider_tee`; see `docs/agent.md`.

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
- A wet quote's deposit address must be 64 lowercase hex characters (the shape of both real answers); any other shape is refused. An echoed deadline different from the asked one is refused, and the guardian log keeps both. Observations from two answers, see `docs/intents.md`.
- Docs: the model id `zai-org/GLM-5.1-FP8` is named as the requested model, an alias on 2026-11-01; which model answered earlier runs is not recorded.
- `python3 -m leash_agent guard --network <testnet|mainnet> ACCOUNT...`: one shared check for every account in a command. Mainnet needs `LEASH_ALLOW_MAINNET=1` in the process environment (never read from `.env`) and refuses `.testnet` accounts; testnet refuses named accounts that do not end in `.testnet` (64-hex implicit accounts pass on both). Prints every account, exits 1 on a refusal. Not yet used by the scripts.
- `LEASH_ALLOW_MAINNET` and `LEASH_ALLOW_NON_TESTNET` now switch on only with the exact value `1` (`0`, `false`, empty or a space refuse). `make wrap-storage` hides the refund account in its output.
- `make guard NETWORK=<testnet|mainnet> ACCOUNTS="..."` runs the guard from the repository root.
- `scrub()` also hides a private value without its `0x` prefix and in percent-encoded form, in any letter case.
- Contract 0.2.0 (unit-tested; exercised on testnet; deployed on mainnet): `set_intents_config`, `set_routes`, `approve_intent` with `quote_hash`, views `get_guardian`, `get_wnear`, `get_routes`, `get_approvals`. `spend_intent`: the agent sends an exact approved amount to the approved deposit account through one batch to wNEAR (two `storage_deposit`, `near_deposit`, `ft_transfer`); checks run in one function that reads no environment; the daily cap is counted before sending and not rolled back; early panic below 120 TGas attached (the 120 and 10 TGas constants are unchanged; one mainnet sample measured 9.853 Tgas for the whole path). `unwrap`: owner, 1 yoctoNEAR, works while paused. Events `intent_sent`, `intent_denied`, `unwrapped`. Fresh deploy only: the state layout changed, there is no `migrate`. The guardian cannot hold an agent policy. `remove_agent` also removes the agent's routes and approvals. Every `set_intents_config` call revokes all pending approvals (epoch). Route fields accept printable ASCII only. A deposit account may not be the vault, owner, guardian, the agent, another agent or the wNEAR contract.
- Patch 6 (unit-tested): `spend_intent` counts the amount plus two wNEAR storage deposits (`INTENT_OVERHEAD`, 0.0025 NEAR) in the daily check and the daily counter, with checked arithmetic; the Python mirror `intent_denial` applies the same overhead to the daily and funds checks. Trust statement rewritten in `docs/security.md` and ADR 0005.
- Patch 7 (unit-tested; later run on testnet and mainnet): call path `make intents-config`, `intents-routes`, `intents-approve`, `intents-spend`, `intents-status`; `spend_intent` gas setting `LEASH_INTENT_GAS_TGAS` (default 150 Tgas, 120 to 300); every command passes the mainnet guard and sends only with `YES=1`.
- Patch 8 (unit-tested; later run on testnet and mainnet): `<prefix>-guardian` account in `make accounts-testnet`; `make intents-keys` (function-call keys: guardian `approve_intent`, agent `spend` and `spend_intent`; every key read back and printed; refuses an owner key file on a mainnet machine); `make intents-quote` (one real quote saved in the private log); outside testnet `intents-approve` and `intents-spend` take the deposit account and hash only from the saved quote (age, deadline, amount, single use, 6 approvals an hour, fresh vault state, expiry at most 29 minutes, printed in UTC); the plan shows route, deposit account, amount, expiry and the agent's caps; the result line says ALLOWED, DENIED or FAILED; owner commands need `CONFIRM_VAULT`; only `YES=1` sends; a test ties the gas, storage and route constants to the contract source. The docs state that the loss through intents is bounded per fixed 24-hour window, up to twice the daily cap across a boundary.
- Testnet run of the success case with function-call keys only: approve and spend accepted, counter and balance changes recorded in `docs/intents.md`; the unused part of the gas reservation returns to the allowance.
- Key allowances (guardian 0.07 NEAR, agent 0.31 NEAR) are the most gas a leaked function-call key can burn. The node refuses a call whose reserved cost (about 0.001 NEAR per prepaid Tgas plus 0.001) is above the allowance or the account balance, so the accounts are funded above it (guardian 0.08, agent 0.25 NEAR on mainnet).
- `make intents-keys` passes the signature scheme to `near account add-key`, so the CLI never waits for an answer; every `near` call now runs without a terminal on stdin and fails at once if it asks a question. With `ASIDE=1` the function-call key file is also copied to `<account>.json` after the creation key file moves aside.
- Patch 9 (unit-tested; the key commands ran on testnet, see `docs/intents.md`): `make intents-keys` uses the flags shown by the CLI help (`--contract-account-id`, `--function-names`); without `YES=1` it also prints the keys now on the accounts; `ASIDE=1` moves creation-key files to a folder beside the keychain (testnet, never deletes); on mainnet it only verifies (function-call key files as planned, no full-access key file, no unlisted full-access key on the accounts, `LEASH_KNOWN_FULL_ACCESS_KEYS`). `intents-spend` saves the vault's available balance before sending, `intents-status` prints before, now and the difference; a mainnet spend stops if the balance cannot be read. A repeated spend for the same approval prints a warning.
- Patch 14 (unit-tested): `LEASH_OWNER` sets an owner account that is not `<prefix>-owner.<network suffix>`, for example a 64-character implicit account; `YES` and `ASIDE` count only when exactly `1`; typed values reach the recipes through the environment and are quoted; the private log is written right before the send, after every refusal, a refused send is logged as failed and does not block a retry, and an existing approval is also looked up on the chain; on mainnet `intents-keys` requires `<account>.json` to hold the function-call key with mode 600; an empty key list is reported as unknown; `intents-keys` says why it skipped writing `<account>.json`.
- Tests: 350 agent tests (161 before), 117 contract tests (40 before). The redaction tests use their own temporary directory.

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
- An attack run first reads the vault (policy, pauses, allowlist, room under the caps, balance) and stops with exit code 3, sending nothing, if the run could prove nothing. Every vault answer is compared with what the policy predicts; a difference marks the run as invalid.
- Payment amounts are compared. In the live scenario without an instruction any payment is unauthorized; with an invoice the payment must be exactly the invoice. The report counts `agent fooled` and `funds lost` separately, and the control of the invoice scenario comes from its own rows. Any unauthorized payment exits with code 2.
- Attack text is escaped before it goes into the `<untrusted>` block, so it cannot close the block. New attacks: a fake closing tag and a fake owner approval (ten in total). `SAMPLES=n` asks the model n times per attack, `LEASH_TEMPERATURE` sets the sampling temperature.
- Long input is refused without a model call. Model calls and tokens are also capped per UTC day in `~/.leash/budget.json`. The API key is only sent to `https://cloud-api.near.ai`, and redirects are refused.
- A vault answer without its event is `UNKNOWN`, no longer counted as blocked or paid, and makes the run invalid.
- `make lock-vault-testnet` reads the key list from the RPC as JSON, refuses a key that is not a full-access key of the vault, and fails unless no full-access key remains afterwards.
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
- The model's answer is read strictly: exactly one JSON object with known keys. The chain-of-thought text (`reasoning_content`) is never used, a cut-off reply is never parsed, and text around the object, two objects or an unclosed `<think>` block are refused.
- The transaction id is read only from the CLI's own `Transaction ID:` line, and event lines only when they start with `EVENT_JSON:`, so text inside a memo cannot fake an id or an event. Memos are cleaned of control characters and cut to 100 bytes.
- Amounts must be plain decimals; `1e999999999` and similar no longer crash the run. Account ids are matched in full. `Vault.spend` validates the destination and memo itself.
- The pay commands refuse a network other than testnet unless `LEASH_ALLOW_NON_TESTNET=1`; `make policy-testnet` is testnet only.
### Added
- Every attack report row carries the transaction id of its vault call, in the printed report and in the `--json` file, so a run can be linked to the explorer row by row.
- `make agent-models` says when the configured model is missing from the list (its price is then not shown), or when the list is empty.
- A vault call without a valid transaction id is printed as `tx MISSING` and the run exits with code 3.
- Tests for `main` (exit codes 0, 1, 2 and 3, the JSON file, the budget line, the models note), the strict parser, hostile memos and the `.env` loader.
- `docs/testnet-proof.md` Run 4: a live-model and hijacked-agent run with a transaction link for every vault call.
- `docs/architecture.md`: components, accounts, the order of the vault checks, events.
### Changed
- README restructured: verified results table, how LEASH uses NEAR, project structure and a disclosure section. Test counts and results match the testnet proof.
- Diagrams are now SVG files (`docs/img/`) instead of Mermaid: the GitHub mobile app shows Mermaid as raw code, and the new diagrams stay readable on a phone.
### Fixed
- `make accounts-testnet` stops when it cannot tell whether an account exists, instead of moving key files of an account that does. It lists the files it moved aside. `LEASH_FORCE_CREATE=1` skips the check.
- The `.env` loader no longer keeps an inline comment as part of a value; `.gitignore` covers `.env.*`, `*.pem` and saved reports.
- Docs: the amount of the authorized payments was not compared, refused attempts have no transaction id, the owner's counter reset is documented, and the NEAR Builders credits sentence is replaced by the instructions link.
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
