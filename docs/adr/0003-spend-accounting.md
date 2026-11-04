# ADR 0003: Spend accounting and the daily window

**Context.** Daily spend must be enforced across many calls.

**Decision.** Spend is counted before the transfer is scheduled and is not rolled back if the transfer fails. The daily window is fixed at 24 hours, starting at the first spend after the previous window ended.

**Consequences.** Accounting fails closed. A fixed window allows up to twice the daily cap across a window boundary. Operators should size funds for that case.

**Intents (extended).** `spend_intent` counts the amount plus `INTENT_OVERHEAD` (two wNEAR storage deposits, 0.0025 NEAR) in the daily cap, in the check and in the counter, so storage burned through tiny intents is bounded by the daily cap per day. A swap needs the amount plus the overhead to fit under the daily cap. If the vault's own storage deposit is refunded, the counter over-counts by 0.00125 NEAR per swap (fails closed; the refund is not yet verified on `wrap.near`). The check uses checked arithmetic: any overflow gives `over_daily_cap`. Rollback on failed transfers and a stricter window are candidate improvements.
