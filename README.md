<p align="center">
  <img src="docs/assets/leash-wordmark.svg" alt="Bylaw: Give AI money. Keep the rules." width="480">
</p>

<p align="center">
  <a href="https://github.com/MusaAis/leash/actions/workflows/ci.yml"><img src="https://github.com/MusaAis/leash/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
  <img src="https://img.shields.io/badge/network-NEAR%20mainnet%20and%20testnet-informational" alt="Network">
  <img src="https://img.shields.io/github/v/release/MusaAis/leash" alt="release">
  <img src="https://img.shields.io/badge/contract%20tests-148%20passed-brightgreen" alt="Contract tests">
  <img src="https://img.shields.io/badge/agent%20tests-767%20passed-brightgreen" alt="Agent tests">
  <img src="https://img.shields.io/badge/Rust-1.96.0-orange" alt="Rust">
  <img src="https://img.shields.io/badge/near--sdk-%3D5.28.3-blue" alt="near-sdk">
</p>

Bylaw is an on-chain spending-control layer for AI agents on NEAR. Funds sit in a vault contract, and an agent can only ask the vault to pay. The vault enforces the owner's limits, so a tricked, buggy or hijacked agent cannot move money outside its policy. Built during NEAR HACKATHON. The commit history shows every date.

Bylaw was developed under the working name `leash`. The repository path, the contract event standard, the accounts and the environment variables keep that name.

**Live demo:** [Arena](https://leash-arena.taila4b466.ts.net:8443/) (testnet) · [Landing page](https://leash-landing-tau.vercel.app/) · [Mainnet proof](docs/mainnet-proof.md) · [Testnet proof](docs/testnet-proof.md)

## Why it matters

Giving an agent a key means trusting it with everything the key controls. Guardrails inside the agent's process do not help, because a compromised agent bypasses them. Bylaw moves the guardrail into a contract the agent cannot change.

## How it works

<p align="center">
  <img src="docs/img/architecture.svg" alt="The owner funds the vault and sets policy. The agent asks the vault to spend. The vault pays the payee if the policy allows it, and otherwise records a denial event." width="440">
</p>

1. The owner funds the vault and sets a policy per agent: per-payment cap, daily cap, destination allowlist.
2. The agent calls `spend(to, amount, memo)`. Its own account holds only gas money.
3. The vault checks the policy. Allowed: the transfer is scheduled. Denied: nothing moves, and a `spend_denied` event with a reason is recorded.
4. The owner can pause one agent or all agents at once, and can always withdraw.
5. For cross-chain payments, a separate guardian account approves one quote-matched payment at a time through NEAR Intents.

## Where NEAR is used

| NEAR feature | Use | Where |
|---|---|---|
| Smart contract | The vault: custody, policy, events, hash-chained decision log | `contract/src/lib.rs` |
| Function-call keys | Agent, guardian and pause-only keys, each limited to named methods | `agent/leash_agent/keys.py`, `panic.py` |
| NEAR Intents (1Click) | The guardian checks each quote; the vault pays the deposit address through wNEAR | `agent/leash_agent/intents.py`, `guardian.py` |
| NEAR AI Cloud | The agent's decisions | `agent/leash_agent/brain.py` |
| Mainnet | One real swap of 0.1 NEAR to USDC on Base; a second vault with allowed and denied payments | [docs/mainnet-proof.md](docs/mainnet-proof.md) |
| Arena | Public prompt-injection test on testnet | [docs/arena.md](docs/arena.md) |

## Verified results

| Check | Result |
|---|---|
| Real swap through the vault on mainnet | 1Click returned status SUCCESS, 0.37853 USDC delivered on Base |
| Current mainnet vault | Allowed and denied `spend`, pause and resume, `chain OK` over the decision events |
| Scripted testnet demo | 12 checks passed, 0 failed |
| Hijacked agent, 14 requests straight to the vault | 11 blocked, 3 paid to the allowed payee inside the caps, 0 paid to anyone else |
| Live model, 10 attack texts, 3 samples each | Agent fooled 0, funds lost 0 |
| Tests | 148 contract and 767 agent tests pass |
| Build | Rust 1.96.0 and near-sdk =5.28.3; wasm hashes in [docs/build.md](docs/build.md) |

The vault, not the model, is the security boundary. Run counts and links: [testnet proof](docs/testnet-proof.md).

## Quick start

Needs Python 3.12 and `make`. The tests also need Rust 1.96.0 with the `wasm32-unknown-unknown` target.

1. No wallet, no keys: `cd agent && python3 -m leash_arena --demo`. See the Arena page served on this machine with a scripted agent.
2. `make test` ends with `test result: ok. 148 passed`.
3. `make test-agent` prints `Ran 767 tests` and then `OK`.
4. Testnet demo (needs [cargo-near](https://github.com/near/cargo-near), [near-cli-rs](https://github.com/near/near-cli-rs) and network): `export LEASH_PREFIX=myleash`, then `make accounts-testnet deploy-testnet demo-testnet`. See `Result: N passed, 0 failed`. Details: [deployment guide](docs/deployment.md).

## Security

- `spend` is enforced entirely on-chain: caps, allowlist, per-agent pause, global kill switch.
- `spend_intent` is enforced on-chain for caps, pauses and a single-use approval that expires within 30 minutes. The guardian checks the quote against the route the owner stored.
- Owner methods that move funds need exactly 1 yoctoNEAR, which a function-call key cannot attach. A leaked pause key can only pause.
- Every decision extends a hash chain. `make chain-check-testnet` prints `chain OK`, or `chain BROKEN` for an altered event.

[Threat model and limits](docs/security.md). Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## Roadmap

- Developer kit: SDK and MCP server
- Shared and team ownership
- Delayed owner changes
- Per-agent cooldown
- Guided vault setup

## Documentation

[Contract API](docs/api.md) · [Architecture](docs/architecture.md) · [Agent runtime](docs/agent.md) · [Arena](docs/arena.md) · [Security](docs/security.md) · [NEAR Intents](docs/intents.md) · [Track fit](docs/tracks.md) · [Index](docs/README.md) · [Changelog](CHANGELOG.md)

## Built with

Rust and near-sdk 5.28.3 (contract); Python standard library only (agent and Arena); cargo-near and near-cli-rs (build and deploy). No other runtime dependencies.

## License

MIT, see [LICENSE](LICENSE).
