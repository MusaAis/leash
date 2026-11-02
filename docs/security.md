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

The agent account holds only gas money; funds stay in the vault. Model output is validated before it is sent, and the vault enforces the policy again, so a fully manipulated model cannot move funds outside the policy. The answer must be exactly one JSON object with known keys, the model's reasoning text is never used, and a cut-off reply is never parsed (see [agent runtime](agent.md)). Outside text is escaped before it is put in the model's untrusted block, long input is refused, the API key is only sent to the NEAR AI Cloud host and redirects are refused, and model use is capped per run and per UTC day.

## Known limitations

- The daily window is fixed. Across a window boundary, up to twice the daily cap can be spent in a short time. Fund agents accordingly.
- Spend is counted before the transfer settles. A failed transfer still consumes cap (fails closed). Verified on testnet: a payment to an allowlisted account that does not exist emitted `spend_allowed`, the vault balance was restored by the refund, and the daily cap stayed consumed (see [testnet proof](testnet-proof.md)).
- The owner key is a single point of control. The owner is fixed at deploy and there is no ownership transfer; losing the key locks the funds. An offline backup key on the owner account is the recovery practice.
- A registered agent can raise the denied counter by sending denied requests. Applications that publish attack statistics should count attempts per session off-chain and not rely on the on-chain counter alone.
- An owner full-access key can reset an agent's daily counter: `remove_agent` deletes the policy and its counter, and `set_policy` then recreates it from zero. A function-call key cannot do either, because both need 1 yoctoNEAR. `set_policy` alone keeps the counter.
- The daily model budget is a file read and written by one process at a time; two runs started together can both pass the check. A service with several processes needs a shared counter.
- Prompt-injection results are evidence about one model and ten attack texts, not a guarantee. The vault policy, not the model, is the security boundary.
- Only native NEAR is spent by the vault. Token and cross-chain payments are not part of this release.
- The guardian's quote checks run off the chain. See [intents](intents.md).
- A compromised guardian can approve intents up to the agent's per-transaction and daily caps. The allowlist does not apply to intents. Routes are records that the guardian enforces, not the contract. Moving funds to a wrong deposit address needs a compromised guardian and a compromised agent at the same time, inside the caps and a 30-minute approval window.
- `spend_intent` counts the amount in the daily cap before the batch runs and does not roll it back. There is no settle callback: a failed swap deposit does not return the cap.
- Changing the intents configuration (`set_intents_config`, even with the same values) revokes every pending approval. `set_routes` clears the approvals of that agent only; it does not change the configuration epoch.
- The agent must attach enough gas for `spend_intent`; below the minimum it panics and nothing is counted. The gas numbers are placeholders until measured in a rehearsal.
- Routes (asset and recipient) are recorded on the chain for the owner and the guardian to read. The vault never sees a swap's destination, so it cannot check that a quote pays the recorded recipient: that check is the guardian's (planned contract 0.2.0, see [ADR 0005](adr/0005-intents-design-and-trust.md)).
- Contract 0.2.0 changes the state layout and has no `migrate`: it is deployed fresh, never over an older vault.

## Exit path

The owner can always call `withdraw` and `unwrap`, also while agents or the whole vault are paused. Deleting the full-access keys removes the ability to upgrade the contract; it does not remove the owner's exit.

## Deployment requirements

Deploy and initialize in one batch. Delete all full-access keys on the vault account before it holds real funds. `make lock-vault-testnet` deletes one key and then checks the key list from the RPC; it was rehearsed on testnet (see [testnet proof](testnet-proof.md), Run 7).

## Reporting

See [SECURITY.md](../SECURITY.md).
