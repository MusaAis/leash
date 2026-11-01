# Project state

- Version: v0.2.3
- Implemented: vault contract (caps, allowlist, pause, kill switch, hardened owner auth), scripted testnet demo with checks, agent runtime with attack suite (ten attacks, preflight, policy prediction, amount checks), daily model budget, vault lock script that verifies from the RPC
- Tests: 40 contract tests, 127 agent tests
- Verified on testnet: asserted demo (12 checks), failed-transfer path, hijacked agent on a fresh policy (14 requests: 11 blocked, 3 paid to the payee inside the caps, 0 to a non-payee), live model (control passed; 10 of 10 attacks refused; 10 of 10 authorized 0.2 NEAR invoices paid exactly; also 30 of 30 and 30 of 30 with three samples at temperature 0.7; agent fooled 0, funds lost 0; 21 and 61 model calls), vault lock rehearsal; see docs/testnet-proof.md
- Next: settlement events and cross-chain payments (NEAR Intents)
