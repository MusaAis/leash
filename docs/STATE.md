# Project state

- Version: v0.3.0
- Implemented: vault contract (caps, allowlist, pause, kill switch, hardened owner auth), scripted testnet demo with checks, 1Click dry quote client and guardian checks, agent runtime with attack suite (ten attacks, preflight, policy prediction, amount checks), daily model budget, vault lock script that verifies from the RPC
- Tests: 40 contract tests, 161 agent tests
- Verified on testnet: asserted demo (12 checks), failed-transfer path, hijacked agent on a fresh policy (14 requests: 11 blocked, 3 paid to the payee inside the caps, 0 to a non-payee), live model (control passed; 10 of 10 attacks refused; 10 of 10 authorized 0.2 NEAR invoices paid exactly; also 30 of 30 and 30 of 30 with three samples at temperature 0.7; agent fooled 0, funds lost 0; 21 and 61 model calls), vault lock rehearsal; see docs/testnet-proof.md
- Verified on mainnet (dry quotes, no funds): nine sizes from 0.5 to 0.001 NEAR passed the guardian's checks; see docs/intents.md
- Next: payments through the vault with NEAR Intents
