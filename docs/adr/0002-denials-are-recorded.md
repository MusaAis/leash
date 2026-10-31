# ADR 0002: Denials are recorded, not reverted

**Context.** A reverted transaction leaves no on-chain state, so blocked attempts cannot be counted or audited.

**Decision.** For accounts that have a policy, `spend` returns `false` and emits a `spend_denied` event with a reason, and the denied counter is updated. Callers without a policy are reverted with no state change, so outsiders cannot inflate the counters.

**Consequences.** Every blocked attempt by a registered agent is auditable. Callers must check the return value.
