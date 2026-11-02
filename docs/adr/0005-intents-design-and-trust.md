# ADR 0005: Intents design and trust

**Status.** In progress. Built and unit-tested on branch `feat/p4b-contract`: routes, guardian, `approve_intent`, `spend_intent`, `unwrap`. Not run on any network. Nothing is deployed on mainnet.

**Context.** The vault holds native NEAR. NEAR Intents (1Click) swaps need a deposit of wNEAR to an address that 1Click returns per quote. The vault must not hand that choice to the agent.

**Decision.** The owner records routes (`dest_asset`, `recipient`, at most 10 per agent). A separate guardian account builds the 1Click request only from the vault's own data, checks the answer, and records a single-use approval (`approve_intent`) with the deposit account, the exact amount, an expiry of at most 30 minutes and a `quote_hash` (sha256 of the saved 1Click answer). The agent can only spend against a matching approval.

**Trust statement.** Routes are records, not enforcement: the vault cannot see a swap's destination. Caps, pauses and approvals are enforced on the chain. The link between a deposit address and the owner's route is checked by the guardian off the chain. `quote_hash` is a guardian assertion for audit; the contract cannot check it.

**Consequences.**
- Moving funds to a wrong deposit address needs a compromised guardian and a compromised agent at the same time, inside the caps and a 30-minute window.
- Contract 0.2.0 changes the state layout. It is deployed fresh. There is no `migrate`.
- The storage deposit of `wrap.near` (0.00125 NEAR, minimum equals maximum) was read once on 2026-11-01 and is not re-checked by the contract.
- `spend_intent` checks run in this order: intents not configured, all paused, agent paused, zero amount, unknown route, over per-transaction cap, no matching approval (same route, deposit account and exact amount, unexpired, current epoch), over daily cap, insufficient funds. A caller without a policy is reverted before any check. The checks live in one function that takes all state as arguments.
- The daily cap counts the amount when the batch is scheduled and is never rolled back. The batch goes to wNEAR as one receipt (`storage_deposit` for the vault, `storage_deposit` for the deposit account, `near_deposit`, `ft_transfer`), so its actions succeed or fail together. If wNEAR changes its storage bounds the batch fails and the cap stays consumed. The two storage deposits are not counted in the daily cap.
- The funds check requires the available balance to cover the amount plus two storage deposits. The same constant is attached to both `storage_deposit` calls.
- `spend_intent` panics before counting anything when less than 120 TGas is attached; each batch action gets 10 TGas. These gas numbers are placeholders, to be measured in a rehearsal.
- `unwrap` (owner, 1 yoctoNEAR) calls `near_withdraw` on wNEAR with 1 yoctoNEAR and needs 30 TGas attached. It touches no cap and works while the vault or an agent is paused. The vault does not wait for the result: a failed `near_withdraw` leaves the `unwrapped` event on the chain.
- `wrap.near` source (the public NEAR core contracts, `near_deposit` and `near_withdraw`) was read once on 2026-11-02. Whether the deployed contract matches that source was not checked on the chain.
- The batch keeps the vault's own `storage_deposit` as its first action. `near_deposit` registers an unregistered caller itself but subtracts the storage minimum from the minted amount, so without that action the first swap would mint too little, the `ft_transfer` would fail, and the cap would stay consumed. Whether `storage_deposit` with `registration_only` refunds the deposit for an already registered vault on `wrap.near` is not verified; the rehearsal records the vault balance before and after.
- `intent_sent` and `unwrapped` are written before the batch runs and there is no callback. They record the request, not the result. Proofs cite the wNEAR receipts and the vault balance.
