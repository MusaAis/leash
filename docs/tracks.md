# Track fit

Bylaw is submitted to four tracks. Each section says what Bylaw does for the track, where to find it, and one step to see it.

## AI Security & Defense

Bylaw gives an agent money without giving it the keys. The vault enforces a per-payment cap, a daily cap, a destination allowlist, a per-agent pause and a global kill switch. The agent and guardian sign with function-call keys limited to named methods. A pause-only key can only pause.

- Code: `contract/src/lib.rs`, `agent/leash_agent/keys.py`, `agent/leash_agent/panic.py`
- Evidence: [mainnet proof](mainnet-proof.md), [Arena](arena.md)
- Do: `cd agent && python3 -m leash_arena --demo`, then send an injected message to the Fortress tier. See the vault refuse every destination except the payee.

## Agent Economy

An agent pays through the vault with its own account, which holds only gas money. For cross-chain payments, the agent supplies a route and an amount. A guardian account approves one matching quote at a time.

- Code: `agent/leash_agent/agent.py`, `agent/leash_agent/swap.py`, `agent/leash_agent/guardian.py`
- Evidence: one real swap of 0.1 NEAR to USDC on Base, in the [mainnet proof](mainnet-proof.md)
- Do: `make test-agent`. See `Ran` followed by the test count and `OK`.

## Unstoppable DeFi

Bylaw settles across chains through NEAR Intents. The guardian checks each 1Click quote field by field. The vault wraps NEAR and sends it to the deposit address in one batch.

- Code: `agent/leash_agent/intents.py`, `contract/src/lib.rs` (`spend_intent`)
- Evidence: [NEAR Intents quotes](intents.md), [mainnet proof](mainnet-proof.md)
- Do: open the mainnet proof and follow the swap transactions. See 1Click status SUCCESS.

## Autonomous Business

The vault acts as a treasury whose limits a human sets and a contract enforces. Cross-chain payments run through NEAR Intents. Bylaw does not include sales, procurement or revenue-sharing logic.

- Code: `contract/src/lib.rs`
- Evidence: [architecture](architecture.md)
- Do: `make test`. See `test result: ok` with the contract test count.
