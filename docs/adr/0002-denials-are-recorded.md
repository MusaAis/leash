# ADR 0002: Denials are recorded, not reverted

**Context.** A reverted transaction leaves no on-chain state, so blocked attempts cannot be counted or audited.

**Decision.** `spend` returns `false` and emits a `spend_denied` event with a reason. Counters are updated.

**Consequences.** Every blocked attempt is auditable. Callers must check the return value.
