# ADR 0001: Vault custody

**Context.** An agent that holds its own funds can spend all of them if it is compromised.

**Decision.** Funds are held by the vault contract. Agents can only request payments through `spend`, and the contract enforces the owner's policy.

**Consequences.** A compromised agent is limited to the policy. The owner can pause one agent or all agents at any time.
