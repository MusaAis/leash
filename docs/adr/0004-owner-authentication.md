# ADR 0004: Owner authentication

**Context.** Owner methods may be called through a restricted function-call key, for example from a dashboard.

**Decision.** Methods that move funds or widen limits require exactly 1 yoctoNEAR, which function-call keys cannot attach. Authorization uses the predecessor account. Methods that only reduce risk (`pause_agent`, `pause_all`) do not require the deposit. Initialization is limited to the contract account.

**Consequences.** A leaked function-call key can pause the vault but cannot withdraw or change policy.
