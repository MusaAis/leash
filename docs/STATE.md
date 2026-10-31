# Project state

- Version: v0.2.0 (candidate)
- Implemented: vault contract (caps, allowlist, pause, kill switch, hardened owner auth), scripted testnet demo with checks, agent runtime with attack suite and model inspection tools
- Tests: 40 contract tests, 38 agent tests
- Verified on testnet: asserted demo (12 checks), failed-transfer path, attack suite in hijacked-agent mode (12 of 12 blocked); see docs/testnet-proof.md
- Next: run the authorized-invoice scenario with the live model, settlement events, cross-chain payments
