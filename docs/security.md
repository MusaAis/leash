# Security model

## Assets

Funds held by the vault contract, the integrity of each agent policy, and the accuracy of the on-chain event log.

## Adversaries and mitigations

| Adversary | Goal | What stops it |
|---|---|---|
| Compromised or manipulated agent, direct payments | Move funds outside policy | `spend` checks the caps, the allowlist and the pauses on-chain. A denied request moves nothing and is recorded. |
| Prompt-injection attacker | Make the agent pay the attacker | For `spend`, any destination not on the allowlist is rejected whatever the agent decides. For `spend_intent`, the agent supplies only a route id and an amount; the deposit address comes from an approval the guardian issued. |
| Compromised agent, cross-chain payments | Send funds to a wrong deposit address | Needs an approval from the guardian for that address, used once, up to the caps, within 30 minutes. |
| Compromised guardian | Approve a wrong deposit address | Cannot spend. Each approval is bounded by the per-transaction cap, at most 5 are pending, and the agent must also spend it. See [Scope and limits](#scope-and-limits). |
| Outsider | Corrupt counters or spam events | Callers without a policy are reverted without state changes. |
| Front-runner at deployment | Take ownership | `new` can only be called by the contract account, in the same batch as deployment. |

## Agent runtime

The agent account holds only gas money; funds stay in the vault. Model output is validated before it is sent, and the vault enforces the policy again, so a fully manipulated model cannot move funds outside the policy. The answer must be exactly one JSON object with known keys, the model's reasoning text is never used, and a cut-off reply is never parsed (see [agent runtime](agent.md)). Outside text is escaped before it is put in the model's untrusted block, long input is refused, the API key is only sent to the NEAR AI Cloud host and redirects are refused, and model use is capped per run and per UTC day.

## Scope and limits

### Accounting and time

- The daily window is fixed at 24 hours. Across a window boundary, up to twice the daily cap can be spent in a short time. Fund agents accordingly.
- Spend is counted before the transfer settles. A failed transfer still consumes cap (it fails closed). A payment to an allowlisted account that does not exist emitted `spend_allowed`, the refund restored the balance, and the cap stayed consumed ([testnet proof](testnet-proof.md)).
- `spend_intent` counts the amount in the daily cap before the batch runs and does not roll it back. There is no settle callback, so a failed swap deposit does not return the cap.
- Each `spend_intent` attaches up to two storage deposits of 0.00125 NEAR to wNEAR. Both count in the daily cap, so `spent_in_window` grows by the amount plus 0.0025 NEAR.
- A swap needs the amount plus 0.0025 NEAR to fit under the daily cap. With the per-transaction cap equal to the daily cap, the largest swap is denied.
- When the vault is already registered on wNEAR, its own storage deposit is refunded, so the counter over-counts by 0.00125 NEAR per swap (it fails closed). A testnet `spend_intent` call showed this; see [intents](intents.md#limits).
- A compromised guardian together with a compromised agent can burn storage deposits with tiny intents, at most up to the daily cap per day.
- The events `intent_sent` and `unwrapped` record the request, not the result. They are written before the batch runs. Evidence of a swap is the wNEAR receipts and the vault balance, never the event alone.

### Keys and ownership

- The owner is fixed at deploy and there is no ownership transfer. Losing the owner key locks the funds. An offline backup key on the owner account is the recovery practice.
- An owner full-access key can reset an agent's daily counter. `remove_agent` deletes the policy and its counter, and `set_policy` then recreates it from zero. A function-call key cannot do either, because both need 1 yoctoNEAR. `set_policy` alone keeps the counter.
- The mainnet vault is not locked. See [Current mainnet state](#current-mainnet-state).
- Contract 0.2.0 changes the state layout and has no `migrate`. It is deployed fresh, never over an older vault.

### Cross-chain path (guardian)

- The guardian is the only off-chain link. Its quote signature is saved, not verified. See [intents](intents.md).
- `spend` is enforced entirely on-chain. `spend_intent` is enforced on-chain for caps, pauses and approvals. The link between a deposit address and the owner's route is checked by the guardian off-chain.
- Routes (asset and recipient) are records on the chain. The vault never sees a swap's destination, so it cannot check that a quote pays the recorded recipient. The guardian checks it.
- The deposit address comes from 1Click. The guardian checks its shape (64 lowercase hex characters). The contract refuses the vault, the owner, the guardian, any agent and the wNEAR account as a deposit account. Nothing else ties the address to the route.
- A false answer can send the amount of one approval to another account without any key being stolen. The loss is bounded by the approval (at most the per-transaction cap, 5 open approvals per agent, 30 minutes) and by the daily cap.
- A compromised guardian key plus a compromised agent can do the same for any address inside the same bounds. The allowlist does not apply to intents.
- Changing the intents configuration (`set_intents_config`, even with the same values) revokes every pending approval. `set_routes` clears the approvals of that agent only.
- The agent must attach enough gas for `spend_intent`. Below the minimum it panics and nothing is counted. The 120 and 10 Tgas constants are unchanged; one mainnet sample burnt 9.853 Tgas for the whole path ([mainnet proof](mainnet-proof.md)).

### Agent runtime and operations

- A registered agent can raise the denied counter by sending denied requests. Applications that publish attack statistics should count attempts per session off-chain.
- The daily model budget is a file read and written by one process at a time. Two runs started together can both pass the check. A service with several processes needs a shared counter.
- Prompt-injection results are evidence about one model and ten attack texts, not a guarantee. The vault policy, not the model, is the security boundary.
- The vault spends native NEAR. A cross-chain payment leaves the vault as wNEAR through `spend_intent`. Other tokens are not supported.

## Exit path

The owner can call `withdraw` and `unwrap` at any time, also while agents or the whole vault are paused.

## Current mainnet state

The mainnet vault `leash-vault.near` is not locked. Its account has one full-access key. The holder of that key can replace the contract, and with it the policy, and can move the vault's funds. The vault stays unlocked while the contract can still change. The lock is planned for the final vault and removes that key.

Each of `leash-guardian.near` and `leash-agent.near` has one full-access key and one function-call key. The guardian's function-call key allows `approve_intent` on the vault. The agent's allows `spend` and `spend_intent`. `make intents-keys` stops when a full-access key file of these accounts is present on the machine it runs on. A holder of a full-access key can act in that account's role inside the caps and can move the account's own balance.

The policy caps, the balances and the dates they were read are in the [table of values read from the chain](mainnet-proof.md#values-read-from-the-chain). Read the balance with `get_available_balance`, the caps with `get_policy` and the keys of any account with `near account list-keys <account> network-config mainnet now`.

## Deployment requirements

Deploy and initialize in one batch. The final vault is locked by deleting its full-access keys. `make lock-vault-testnet` deletes one key and then checks the key list from the RPC; it was run on a testnet vault (see [testnet proof](testnet-proof.md), Run 7).

## Reporting

See [SECURITY.md](../SECURITY.md).
