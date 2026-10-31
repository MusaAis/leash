# Project state

- Version: v0.2.2 (candidate); v0.2.1 is tagged and released
- Implemented: vault contract (caps, allowlist, pause, kill switch, hardened owner auth), scripted testnet demo with checks, agent runtime with attack suite and model inspection tools
- Tests: 40 contract tests, 67 agent tests
- Verified on testnet: asserted demo (12 checks), failed-transfer path, hijacked-agent attack suite (12 of 12 blocked), clean live-model run (control passed, 8 of 8 attacks refused, 8 of 8 payments to the authorized payee, 0 redirected; destination checked, amount not compared; 17 model calls, 10,197 tokens; re-run on the v0.2.2 code with a transaction link for every vault call, 9,411 tokens); see docs/testnet-proof.md
- Next: review of v0.2.x, then settlement events and cross-chain payments (NEAR Intents)
