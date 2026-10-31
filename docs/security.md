# Security model

## Assets

Funds held by the vault contract, the integrity of each agent policy, and the accuracy of the on-chain event log.

## Adversaries and mitigations

| Adversary | Goal | Mitigation |
|---|---|---|
| Compromised or manipulated agent | Move funds outside policy | Funds are held by the vault. `spend` enforces per-transaction cap, daily cap and destination allowlist on-chain. |
| Prompt-injection attacker | Make the agent send funds to the attacker | The contract rejects any destination not on the allowlist, regardless of what the agent decides. |
| Leaked function-call key of the owner | Withdraw or change policy | Fund-moving owner methods require 1 yoctoNEAR. Such a key can only pause. |
| Outsider | Corrupt counters or spam events | Callers without a policy are reverted without state changes. |
| Front-runner at deployment | Take ownership | `new` can only be called by the contract account, in the same batch as deployment. |

## Known limitations

- The daily window is fixed. Across a window boundary, up to twice the daily cap can be spent in a short time. Fund agents accordingly.
- Spend is counted before the transfer settles. A failed transfer still consumes cap (fails closed).
- The owner key is a single point of control. Loss of the key locks funds. A guardian and two-step ownership transfer are planned.
- Only native NEAR is supported. Token support, cooldowns and human approval above a threshold are planned.

## Deployment requirements

Deploy and initialize in one batch. Delete all full-access keys on the vault account before it holds real funds, and verify on an explorer.

## Reporting

See [SECURITY.md](../SECURITY.md).
