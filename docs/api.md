# Contract API

The vault contract's methods, events and denial reasons. Method names, event names and denial reasons are checked against the contract source by `make check-docs`. Field lists and notes are not machine-checked.

## Methods

| Method | Caller | Notes |
|---|---|---|
| `new(owner)` | Vault account | Initialization, once |
| `fund()` | Anyone | Payable |
| `withdraw(amount)` | Owner | Requires 1 yoctoNEAR |
| `set_policy(agent, per_tx_cap, daily_cap, allowlist)` | Owner | Requires 1 yoctoNEAR; keeps the running daily counter |
| `remove_agent(agent)` | Owner | Requires 1 yoctoNEAR |
| `pause_agent(agent)`, `pause_all()` | Owner | No deposit needed: these only reduce risk |
| `resume_agent(agent)`, `resume_all()` | Owner | Requires 1 yoctoNEAR |
| `spend(to, amount, memo)` | Agent | Returns `true` if scheduled, `false` if denied |
| `set_intents_config(wnear, guardian)` | Owner | Requires 1 yoctoNEAR; revokes every pending approval |
| `set_routes(agent, routes)` | Owner | Requires 1 yoctoNEAR; up to 10 routes (destination asset and recipient) per agent; clears that agent's approvals |
| `approve_intent(agent, route_id, deposit_account, amount, expires_ns, quote_hash)` | Guardian | Single-use approval, at most 30 minutes, at most 5 pending per agent |
| `spend_intent(route_id, deposit_account, amount)` | Agent | Same checks as `spend` plus a matching approval; pays the deposit address through wNEAR |
| `unwrap(amount)` | Owner | Requires 1 yoctoNEAR; converts wNEAR back to NEAR, also while paused |
| `get_policy`, `get_stats`, `get_owner`, `is_paused_all`, `get_available_balance`, `get_guardian`, `get_wnear`, `get_routes`, `get_approvals`, `get_head_hash` | Anyone | Views |
| `would_spend(agent, to, amount, memo)`, `would_spend_intent(agent, route_id, deposit_account, amount)` | Anyone | Views that run the same checks as `spend` and `spend_intent` and return `allowed` or the denial reason |

## Events

Each event is logged as `EVENT_JSON:` followed by `{"standard":"leash","version":...,"event":...,"data":[...]}`.

| Event | Data fields |
|---|---|
| `funded` | `from`, `amount` |
| `withdrawn` | `amount` |
| `policy_set` | `agent`, `per_tx_cap`, `daily_cap` |
| `agent_removed`, `agent_paused`, `agent_resumed` | `agent` |
| `paused_all`, `resumed_all` | none |
| `spend_allowed` | `agent`, `to`, `amount`, `memo` |
| `spend_denied` | `agent`, `to`, `amount`, `reason`, `memo` |
| `intents_configured` | `wnear`, `guardian`, `epoch` |
| `routes_set` | `agent`, `count` |
| `intent_approved` | `agent`, `route_id`, `deposit_account`, `amount`, `expires_ns`, `quote_hash` |
| `intent_sent` | `agent`, `route_id`, `deposit_account`, `amount`, `quote_hash` |
| `intent_denied` | `agent`, `route_id`, `deposit_account`, `amount`, `reason` |
| `unwrapped` | `amount` |

Amounts are in yoctoNEAR, as strings.

## Denial reasons

Denial reasons: `all_paused`, `memo_too_long`, `agent_paused`, `zero_amount`, `over_per_tx_cap`, `not_allowlisted`, `over_daily_cap`, `insufficient_funds`.

`spend_intent` shares these except `memo_too_long` and `not_allowlisted`, and adds `intents_not_configured`, `unknown_route` and `no_approval`.

For an account without a policy the real methods revert, and the two `would_spend` views return `no_policy`.

## Related documents

[Architecture](architecture.md) · [Security model](security.md) · [NEAR Intents quotes](intents.md)
