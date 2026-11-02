# ADR 0006: Guardian role

**Status.** In progress (see ADR 0005).

**Context.** Something must approve each swap deposit before the agent can send funds to it.

**Decision.** One guardian account per vault, set by the owner with `set_intents_config` (1 yoctoNEAR). It must differ from the owner, the vault and every agent, and an agent policy cannot be created for it. It can call `approve_intent` only: it cannot spend, withdraw or change policy. Every call of `set_intents_config`, also with the same values, bumps an epoch and so revokes all pending approvals. Approvals also expire after at most 30 minutes and are limited to 5 per agent.

**Consequences.** A leaked guardian key cannot move funds alone, because the agent must also call `spend_intent`. After a suspected leak the owner rotates the guardian and pending approvals stop matching at once.
