# ADR 0003: Spend accounting and the daily window

**Context.** Daily spend must be enforced across many calls.

**Decision.** Spend is counted before the transfer is scheduled and is not rolled back if the transfer fails. The daily window is fixed at 24 hours, starting at the first spend after the previous window ended.

**Consequences.** Accounting fails closed. A fixed window allows up to twice the daily cap across a window boundary. Operators should size funds for that case. Rollback on failed transfers and a stricter window are candidate improvements.
