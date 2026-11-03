# Project state

- Version: v0.3.0
- Implemented: vault contract (caps, allowlist, pause, kill switch, hardened owner auth), scripted testnet demo with checks, 1Click dry quote client and guardian checks, agent runtime with attack suite (ten attacks, preflight, policy prediction, amount checks), daily model budget, vault lock script that verifies from the RPC
- Tests: 117 contract tests, 350 agent tests
- Verified on testnet: asserted demo (12 checks), failed-transfer path, hijacked agent on a fresh policy (14 requests: 11 blocked, 3 paid to the payee inside the caps, 0 to a non-payee), live model (control passed; 10 of 10 attacks refused; 10 of 10 authorized 0.2 NEAR invoices paid exactly; also 30 of 30 and 30 of 30 with three samples at temperature 0.7; agent fooled 0, funds lost 0; 21 and 61 model calls), vault lock rehearsal; see docs/testnet-proof.md
- Checked against the live 1Click mainnet API (dry quotes, no funds; no LEASH contract is on mainnet): nine sizes from 0.5 to 0.001 NEAR passed the guardian's checks; see docs/intents.md
- In progress (branch `feat/p4b-contract`, not merged, not deployed): contract 0.2.0 with guardian, routes, single-use approvals (`approve_intent`), unit-tested only, not run on any network. It adds `spend_intent` (agent, one approved swap deposit through wNEAR) and `unwrap` (owner)
- Also on that branch (unit-tested, not run on a chain): the call path `make intents-keys`, `intents-config`, `intents-routes`, `intents-quote`, `intents-approve`, `intents-spend`, `intents-status`. Executing a swap through the vault is implemented and unit-tested; no live swap has been done
- Next: a rehearsal of the swap path on a test network, then a first small swap
