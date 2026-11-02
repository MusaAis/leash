<p align="center">
  <img src="docs/assets/leash-wordmark.svg" alt="LEASH: Give AI money. Keep the leash." width="480">
</p>

<p align="center">
  <a href="https://github.com/MusaAis/leash/actions/workflows/ci.yml"><img src="https://github.com/MusaAis/leash/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
  <img src="https://img.shields.io/badge/network-NEAR%20testnet-informational" alt="Network">
</p>

LEASH is an on-chain spending-control layer for AI agents on NEAR. The funds sit in a vault contract, and an agent can only ask the vault to pay. The vault enforces the owner's limits on-chain, so an agent that is tricked, buggy or hijacked cannot move money outside its policy. Built for NEAR HACKATHON.

[Why it matters](#why-it-matters) · [How it works](#how-it-works) · [Verified results](#verified-results) · [Quick start](#quick-start) · [Agent runtime](#agent-runtime-and-attack-suite) · [NEAR usage](#how-leash-uses-near) · [Documentation](#documentation)

## Why it matters

Giving an AI agent a key means trusting it with everything that key controls. If the agent is tricked by prompt injection, buggy or compromised, the funds are gone. Guardrails inside the agent's own process do not help, because a compromised agent bypasses them. LEASH moves the guardrail out of the agent and into a contract the agent cannot change.

## How it works

<p align="center">
  <img src="docs/img/architecture.svg" alt="The owner funds the vault and sets policy. The agent asks the vault to spend. The vault pays the payee if the policy allows it, and otherwise records a denial event." width="440">
</p>

1. The owner funds the vault and sets a policy per agent.
2. The agent calls `spend(to, amount, memo)`. The agent account holds only gas money.
3. The contract checks the policy. Allowed: the transfer is scheduled and a `spend_allowed` event is recorded. Denied: nothing moves, and a `spend_denied` event with a reason is recorded and counted on-chain.
4. The owner can pause one agent or every agent at any time. The owner can always withdraw.

### What the vault checks

<p align="center">
  <img src="docs/img/spend-checks.svg" alt="The vault checks, in order: caller has a policy, kill switch, memo length, agent pause, zero amount, per-payment cap, allowlist, daily cap, available funds. The first check that applies denies the payment." width="440">
</p>

| Rule | Description |
|---|---|
| Per-transaction cap | Maximum amount for a single payment |
| Daily cap | Maximum total per fixed 24-hour window |
| Destination allowlist | Up to 20 accounts the agent may pay |
| Agent pause | Blocks one agent immediately |
| Global kill switch | Blocks every agent immediately |

## Verified results

Measured on NEAR testnet. Every row has its evidence in [docs/testnet-proof.md](docs/testnet-proof.md), and the latest run links every vault call to the explorer.

| Check | Result |
|---|---|
| Contract unit tests | 117 passed |
| Agent unit tests | 238 passed |
| 1Click dry quotes against the live mainnet API, no funds, no LEASH contract on mainnet | 9 sizes from 0.5 to 0.001 NEAR passed the guardian's checks, see [docs/intents.md](docs/intents.md) |
| Scripted testnet demo (`make demo-testnet`) | 12 checks passed, 0 failed |
| Hijacked agent on a fresh policy, every request sent straight to the vault | 14 requests: 11 blocked by the vault, 3 paid to the allowlisted payee inside the caps (by design), 0 paid to a non-payee |
| Live model, attack text only | Control task passed. 10 of 10 attacks refused by the agent, and 30 of 30 with three samples per attack at temperature 0.7 |
| Live model, authorized 0.2 NEAR invoice with the attack inside it | 10 of 10 payments were exactly the authorized amount to the authorized payee, and 30 of 30 decisions with three samples. Agent fooled 0, funds lost 0 |
| Cost of the live runs | 21 model calls and 10,527 tokens (one sample); 61 calls and 31,871 tokens (three samples) |

These are two live runs with ten attack texts and one model, one at temperature 0 and one with three samples per attack at temperature 0.7. The model was not fooled in either, so in the authorized-invoice scenario the vault had nothing to block there. What the vault does against a fully fooled agent is what the hijacked-agent run shows. This is not a proof of robustness.

## Quick start

Prerequisites:

- Rust (stable) with the `wasm32-unknown-unknown` target, and `make`
- Python 3.12, the version CI and the tests run on (agent runtime and its tests; no third-party packages; older versions are untested)
- For the wasm build and the testnet demo only: [cargo-near](https://github.com/near/cargo-near), [near-cli-rs](https://github.com/near/near-cli-rs) and network access. See the [deployment guide](docs/deployment.md).

Run the tests:

```
make test         # contract tests
make test-agent   # agent tests
make check        # format check, lint, contract and agent tests
```

Expected: `make test` ends with `test result: ok. 117 passed`, and `make test-agent` prints `Ran 238 tests` followed by `OK`.

### Testnet demo (optional, needs network)

A scripted run with a pass/fail check per step: the owner sets a policy, the agent pays an allowed payee, two attacks are blocked, the daily cap is exhausted, one agent is paused, the kill switch stops everything, payments resume, and a failed transfer is handled.

```
export LEASH_PREFIX=myleash   # a unique name; use a fresh one for every demo run
make accounts-testnet         # create five faucet-funded testnet accounts
make deploy-testnet           # build, deploy and initialize the vault in one transaction
make demo-testnet             # run the scripted scenario
```

Expected: the demo ends with `Result: N passed, 0 failed`. Allowed payments return `true`. Blocked ones return `false` and emit a `spend_denied` event with the reason. Use a fresh prefix for every run, because the daily cap (3 NEAR) carries over on the same vault. If the testnet faucet answers `429`, the account script waits and retries. Details: [deployment guide](docs/deployment.md).

## Agent runtime and attack suite

A Python agent (standard library only) asks a NEAR AI Cloud model whether to pay, then calls the vault. An attack suite tries ten kinds of manipulation against it: instruction override, fake admin notice, social engineering, encoded payload, tool-output injection, look-alike address, oversized payment, a payment split into small parts, a fake closing tag that tries to break out of the untrusted block, and a fake owner approval.

```
cp .env.example .env     # set LEASH_PREFIX and NEAR_AI_API_KEY
make agent-check         # configuration and vault counters
make policy-testnet      # test vaults only: fresh policy and funds, without the full demo
make attack-testnet      # hijacked agent: what does the vault block?
make attack-llm-testnet  # the real model sees every attack text
make attack-llm-testnet SAMPLES=3  # ask the model three times per attack (with LEASH_TEMPERATURE=0.7)
```

Before it sends anything, the suite checks the vault (policy, pauses, allowlist, room under the caps, balance) and stops with exit code 3 if a run would prove nothing. The report lists every attempt with its outcome and ends with totals, including `agent fooled` (the model decided to pay what it should not) and `funds lost` (money that really left the vault without authorization). The command exits with code 2 if a payment left the vault without authorization. Live runs are capped per run and per day (`NEAR_AI_MAX_TOKENS`, `LEASH_MAX_MODEL_CALLS`, `LEASH_MAX_TOTAL_TOKENS`, `LEASH_MAX_DAILY_CALLS`, `LEASH_MAX_DAILY_TOKENS`), so a mistake cannot spend the credits. Details: [agent runtime](docs/agent.md).

## How LEASH uses NEAR

| NEAR feature | Use in LEASH | State |
|---|---|---|
| Smart contract (Rust, near-sdk 5) | The vault: custody, policy engine, event log | Implemented, deployed on testnet |
| Separate named accounts | Vault, owner, agent and payee are different accounts, so the agent never holds the funds | Implemented |
| One-yoctoNEAR rule | Owner methods that move funds or widen limits need exactly 1 yoctoNEAR, which function-call keys cannot attach. A leaked function-call key can only pause | Implemented and tested |
| Full-access key removal | `make lock-vault-testnet` deletes the vault's full-access keys, so no one can bypass the policy or replace the contract | Script ready, for the final vault |
| NEP-297 style events | `EVENT_JSON:` logs with standard `leash` for every decision | Implemented |
| NEAR AI Cloud | The agent's decisions, through its OpenAI-compatible API | Implemented |
| NEAR Intents (1Click) | The guardian builds and checks dry quotes. No payment goes through the vault yet | Quotes implemented, payments planned |
| Chain Signatures | Not used in this release | Roadmap |

## Contract interface

| Method | Caller | Notes |
|---|---|---|
| `new(owner)` | Vault account | Initialization, once |
| `fund()` | Anyone | Payable |
| `withdraw(amount)` | Owner | Requires 1 yoctoNEAR |
| `set_policy(agent, per_tx_cap, daily_cap, allowlist)` | Owner | Requires 1 yoctoNEAR; keeps the running daily counter |
| `remove_agent(agent)` | Owner | Requires 1 yoctoNEAR |
| `pause_agent(agent)`, `pause_all()` | Owner | No deposit needed: these only reduce risk |
| `resume_agent(agent)`, `resume_all()` | Owner | Requires 1 yoctoNEAR |
| `spend(to, amount, memo)` | Agent | Returns `true` if scheduled, `false` if denied |
| `get_policy`, `get_stats`, `get_owner`, `is_paused_all`, `get_available_balance` | Anyone | Views |

Events: `funded`, `withdrawn`, `policy_set`, `agent_removed`, `agent_paused`, `agent_resumed`, `paused_all`, `resumed_all`, `spend_allowed`, `spend_denied`. Denial reasons: `all_paused`, `memo_too_long`, `agent_paused`, `zero_amount`, `over_per_tx_cap`, `not_allowlisted`, `over_daily_cap`, `insufficient_funds`.

## Security

The agent is never the last line of defense: the contract rejects any destination, amount or rate outside the policy, whatever the agent decides. The [security model](docs/security.md) lists the adversaries and mitigations. Main known limitations:

- The daily window is fixed, so across a window boundary up to twice the daily cap can be spent in a short time.
- Spend is counted before the transfer settles. A failed transfer still uses up the cap (it fails closed).
- The owner key is a single point of control. There is no ownership transfer, and losing the key locks the funds.
- Only native NEAR is supported.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Project structure

```
contract/            Rust vault contract and its unit tests
agent/leash_agent/   agent runtime: brain (model), chain (vault client), attacks, intents and guardian (1Click quotes), CLI
agent/tests/         agent unit tests
scripts/             testnet accounts, deploy, demo, policy, lock and release scripts
docs/                architecture, agent runtime, deployment, security, testnet proof, ADRs
Makefile             short entry points for every command above
```

## Status

| Feature | State |
|---|---|
| Per-transaction cap, daily cap, destination allowlist | Implemented |
| Per-agent pause, global kill switch | Implemented |
| Owner methods protected against function-call key leaks | Implemented |
| Event log and on-chain allow/deny counters | Implemented |
| Testnet deployment and scripted demo | Done, see [proof](docs/testnet-proof.md) |
| Agent runtime with NEAR AI Cloud decisions and attack suite | Implemented, tested on testnet |
| Mainnet deployment | Planned |
| 1Click dry quotes with a guardian check | Implemented, live dry quotes recorded in [docs/intents.md](docs/intents.md) |
| Cross-chain payments through the vault, Arena, SDK | Planned |

## Documentation

- [Architecture](docs/architecture.md)
- [Agent runtime](docs/agent.md)
- [Deployment guide](docs/deployment.md)
- [Security model](docs/security.md)
- [NEAR Intents quotes](docs/intents.md)
- [Testnet proof](docs/testnet-proof.md)
- [Architecture decisions](docs/adr)
- [Changelog](CHANGELOG.md)

## Pre-existing code

No pre-existing code is included in this repository. Any later integration of earlier work will live in its own directory and be listed here with a link to the diff.

## License

MIT, see [LICENSE](LICENSE).
