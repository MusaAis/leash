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

## Agent runtime

The agent account holds only gas money; funds stay in the vault. Model output is validated before it is sent, and the vault enforces the policy again, so a fully manipulated model cannot move funds outside the policy. The answer must be exactly one JSON object with known keys, the model's reasoning text is never used, and a cut-off reply is never parsed (see [agent runtime](agent.md)). See [agent runtime](agent.md).

## Known limitations

- The daily window is fixed. Across a window boundary, up to twice the daily cap can be spent in a short time. Fund agents accordingly.
- Spend is counted before the transfer settles. A failed transfer still consumes cap (fails closed). Verified on testnet: a payment to an allowlisted account that does not exist emitted `spend_allowed`, the vault balance was restored by the refund, and the daily cap stayed consumed (see [testnet proof](testnet-proof.md)).
- The owner key is a single point of control. Loss of the key locks funds. A guardian and two-step ownership transfer are planned.
- A registered agent can raise the denied counter by sending denied requests. Applications that publish attack statistics should count attempts per session off-chain and not rely on the on-chain counter alone.
- An owner full-access key can reset an agent's daily counter: `remove_agent` deletes the policy and its counter, and `set_policy` then recreates it from zero. A function-call key cannot do either, because both need 1 yoctoNEAR. `set_policy` alone keeps the counter.
- Only native NEAR is supported. Token support, cooldowns and human approval above a threshold are planned.

## Exit path

The owner can always call `withdraw`, also while agents or the whole vault are paused. Deleting the full-access keys removes the ability to upgrade the contract; it does not remove the owner's exit.

## Deployment requirements

Deploy and initialize in one batch. Delete all full-access keys on the vault account before it holds real funds, and verify on an explorer.

## Reporting

See [SECURITY.md](../SECURITY.md).
