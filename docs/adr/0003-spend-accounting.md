# ADR 0003: Spend accounting and the daily window

**Context.** Daily spend must be enforced across many calls.

**Decision.** Spend is counted before the transfer is scheduled and is not rolled back if the transfer fails. The daily window is a fixed 24 hours starting at the first spend after the previous window ended.

**Consequences.** Accounting fails closed. Rollback on failed transfers is a candidate improvement.
