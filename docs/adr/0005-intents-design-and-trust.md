# ADR 0005: Intents design and trust

**Status.** In progress. Built and unit-tested on branch `feat/p4b-contract`: routes, guardian, `approve_intent`. Not built: `spend_intent`, `unwrap`. Nothing is deployed on mainnet.

**Context.** The vault holds native NEAR. NEAR Intents (1Click) swaps need a deposit of wNEAR to an address that 1Click returns per quote. The vault must not hand that choice to the agent.

**Decision.** The owner records routes (`dest_asset`, `recipient`, at most 10 per agent). A separate guardian account builds the 1Click request only from the vault's own data, checks the answer, and records a single-use approval (`approve_intent`) with the deposit account, the exact amount, an expiry of at most 30 minutes and a `quote_hash` (sha256 of the saved 1Click answer). The agent can only spend against a matching approval.

**Trust statement.** Routes are records, not enforcement: the vault cannot see a swap's destination. Caps, pauses and approvals are enforced on the chain. The link between a deposit address and the owner's route is checked by the guardian off the chain. `quote_hash` is a guardian assertion for audit; the contract cannot check it.

**Consequences.**
- Moving funds to a wrong deposit address needs a compromised guardian and a compromised agent at the same time, inside the caps and a 30-minute window.
- Contract 0.2.0 changes the state layout. It is deployed fresh. There is no `migrate`.
- The storage deposit of `wrap.near` (0.00125 NEAR, minimum equals maximum) was read once on 2026-11-01 and is not re-checked by the contract.
