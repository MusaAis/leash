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

## Scope and limits

- The daily window is fixed. Across a window boundary, up to twice the daily cap can be spent in a short time. Fund agents accordingly.
- Spend is counted before the transfer settles. A failed transfer still consumes cap (fails closed). Verified on testnet: a payment to an allowlisted account that does not exist emitted `spend_allowed`, the vault balance was restored by the refund, and the daily cap stayed consumed (see [testnet proof](testnet-proof.md)).
- The owner key is a single point of control. The owner is fixed at deploy and there is no ownership transfer; losing the key locks the funds. An offline backup key on the owner account is the recovery practice.
- A registered agent can raise the denied counter by sending denied requests. Applications that publish attack statistics should count attempts per session off-chain and not rely on the on-chain counter alone.
- An owner full-access key can reset an agent's daily counter: `remove_agent` deletes the policy and its counter, and `set_policy` then recreates it from zero. A function-call key cannot do either, because both need 1 yoctoNEAR. `set_policy` alone keeps the counter.
- The daily model budget is a file read and written by one process at a time; two runs started together can both pass the check. A service with several processes needs a shared counter.
- Prompt-injection results are evidence about one model and ten attack texts, not a guarantee. The vault policy, not the model, is the security boundary.
- Only native NEAR is spent by the vault. Token and cross-chain payments are not part of this release.
- The guardian is the only off-chain link, and its quote signature is not verified. See [intents](intents.md).
- A compromised guardian can approve intents up to the agent's per-transaction and daily caps. (Per fixed 24-hour window; across a window boundary, up to twice the daily cap.) The allowlist does not apply to intents. Routes are records that the guardian enforces, not the contract. `spend` is enforced entirely on-chain; `spend_intent` is enforced on-chain for caps, pauses and approvals, but the link between a deposit address and the owner's route is checked by the guardian off-chain. The deposit address comes from 1Click. The guardian checks its shape (64 lowercase hex characters) and the contract refuses the vault, the owner, the guardian, any agent and the wNEAR account as a deposit account; nothing else ties the address to the route, and the quote signature is saved, not verified. A false answer can send the amount of one approval to another account without any key being stolen. The loss is bounded by the approval (at most the per-transaction cap, 5 open approvals per agent, 30 minutes) and by the daily cap. A compromised guardian key plus a compromised agent can do the same for any address inside the same bounds. The daily cap applies per fixed 24-hour window: across a window boundary the loss can reach up to twice the daily cap.
- Each `spend_intent` also attaches up to two storage deposits of 0.00125 NEAR each to wNEAR. Both are counted in the daily cap (`spent_in_window` grows by the amount plus 0.0025 NEAR, so `get_policy` shows the overhead), and a swap needs the amount plus 0.0025 NEAR to fit under the daily cap: with the per-transaction cap equal to the daily cap, the largest swap is denied. The vault's own deposit is expected to be refunded when the vault is already registered (not yet verified on `wrap.near`; the rehearsal records the vault balance before and after), so the counter may over-count by 0.00125 NEAR per swap (fails closed). A compromised guardian and agent can still burn storage deposits with tiny intents, but only up to the daily cap per day.
- The events `intent_sent` and `unwrapped` record the request, not the result: they are written before the batch runs. A failed batch leaves the event on the chain and the cap consumed. Evidence of a swap is the wNEAR receipts and the vault balance, never the event alone.
- `spend_intent` counts the amount in the daily cap before the batch runs and does not roll it back. There is no settle callback: a failed swap deposit does not return the cap.
- Changing the intents configuration (`set_intents_config`, even with the same values) revokes every pending approval. `set_routes` clears the approvals of that agent only; it does not change the configuration epoch.
- The agent must attach enough gas for `spend_intent`; below the minimum it panics and nothing is counted. The 120 and 10 TGas constants are unchanged; one mainnet sample measured 9.853 Tgas for the whole path (see the mainnet proof).
- Routes (asset and recipient) are recorded on the chain for the owner and the guardian to read. The vault never sees a swap's destination, so it cannot check that a quote pays the recorded recipient: that check is the guardian's. The contract is unit-tested, and one live swap went through it on mainnet (see the [mainnet proof](mainnet-proof.md) and [ADR 0005](adr/0005-intents-design-and-trust.md)).
- Contract 0.2.0 changes the state layout and has no `migrate`: it is deployed fresh, never over an older vault.

## Exit path

The owner can always call `withdraw` and `unwrap`, also while agents or the whole vault are paused. Deleting the full-access keys removes the ability to upgrade the contract; it does not remove the owner's exit.

## Deployment requirements

Deploy and initialize in one batch. Delete all full-access keys on the vault account before it holds real funds. `make lock-vault-testnet` deletes one key and then checks the key list from the RPC; it was rehearsed on testnet (see [testnet proof](testnet-proof.md), Run 7).

## Reporting

See [SECURITY.md](../SECURITY.md).
