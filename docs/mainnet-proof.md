# Mainnet proof

One vault on NEAR mainnet and one real swap through it, run on 2026-11-04. Every transaction below links to the explorer. Recorded values are read from the command output and the explorer; nothing here is estimated unless it says so.

## Accounts

- Vault: `leash-vault.near` (contract 0.2.0, not locked)
- Guardian: `leash-guardian.near`, a function-call key limited to `approve_intent` on the vault
- Agent: `leash-agent.near`, a function-call key limited to `spend` and `spend_intent` on the vault
- Owner: the implicit account `839d44f94fb99bb1412c68f2fa8534a8a739a1478d8bb1dc97663d02cca51d09`. Its full-access key stays on the owner's device; no server holds it (the owner's statement).
- At the time of the swap the server's key files held function-call keys only: `make intents-keys` printed OK for this check before the swap.

## Transactions

| Step | Transaction |
|---|---|
| Create `leash-guardian.near` (0.08 NEAR) | [AQKtQp…q9Cip](https://explorer.near.org/transactions/AQKtQpmTmj5XFCzKSg7Am1xsQqjpZJxxjdoZvYrq9Cip) |
| Create `leash-agent.near` (0.25 NEAR) | [G7ExWJ…wGRiQ](https://explorer.near.org/transactions/G7ExWJaCroutmL73kjftG9DpgmsmhJb76sVPVw6wGRiQ) |
| Create `leash-vault.near` (2.10 NEAR) | [AsbJeY…BAeqF](https://explorer.near.org/transactions/AsbJeYKTep67T1VRzEeF6yeKqXAT1i2rrf4cAjkBAeqF) |
| Deploy contract and call `new` | [J5kcsR…jFKVn](https://explorer.near.org/transactions/J5kcsRNxYg7dkCD2JTjojxpijvTmdU8dmQfoAenjFKVn) |
| `set_policy`: per-transaction cap 0.15 NEAR, daily cap 0.30 NEAR, one allowlisted account | [BV7Mw3…Pwasg](https://explorer.near.org/transactions/BV7Mw3WmYPeqbTaPiVWmeD1Ke2NQZTdGshR7L2YPwasg) |
| `fund` 0.3 NEAR | [ENBcu9…KBtps](https://explorer.near.org/transactions/ENBcu9JFDHTtnFWDngB5HnouoMKw5yCd4YzjJLHKBtps) |
| `set_intents_config` (wNEAR `wrap.near`, guardian `leash-guardian.near`) | [2m5ar1…CjpEo](https://explorer.near.org/transactions/2m5ar1LMFE1bYMpLxVkyr31kNJoMtzR4aZteh32CjpEo) |
| `set_routes`: one route to USDC on Base | [8NFxBa…RLF8q](https://explorer.near.org/transactions/8NFxBaFayGaXPxqWqtjbHh6pV5YCcuwjynQhzXqRLF8q) |
| Guardian function-call key (`approve_intent` only, allowance 0.07 NEAR) | [7edueu…bYBvX](https://explorer.near.org/transactions/7edueu2iwmLDwPcdH9Z47vDjhEQmGcv7PAJJXaabYBvX) |
| Agent function-call key (`spend`, `spend_intent`, allowance 0.31 NEAR) | [2S111W…UnYKq](https://explorer.near.org/transactions/2S111WsSLVNXGpp42WgD7UeeKcdkv8q94qkrGjRUnYKq) |
| `approve_intent` by the guardian: 0.1 NEAR, event `intent_approved` | [HrqjGQ…t7R8r](https://explorer.near.org/transactions/HrqjGQtCWNXq2MksL6sDaQDSjWjk5LCHyfvydK9t7R8r) |
| `spend_intent` by the agent: 0.1 NEAR, event `intent_sent` | [AcF8rw…jTyD2](https://explorer.near.org/transactions/AcF8rwjBTdKCiofwRx1yCabqScBLEmTvsazxHWnjTyD2) |

The quote is a 1Click request and has no transaction. The deployed code hash is `FpXfPq1ejnsvDVAV3LdRD4RAkGVEPfSAmxecYHPUMq1i` (base58), which is the SHA-256 `dc3095a72ed04e2c1dddedaeae5e25044d22ca0fe8426e93643efa01d86858f9` of a 189,825-byte wasm. The same file ran `make demo-testnet` (12 checks passed) and a `spend_intent` rehearsal on testnet before it went to mainnet.

## The swap

- Quote: 0.1 NEAR in, 378,371 units of USDC out (6 decimals, 0.378371 USDC), minimum 374,587, estimate 24 seconds. The guardian verified every field before approving.
- Approval: expiry 05:17:07 UTC, 17 minutes; the spend ran at 05:02:58 UTC (block 222,973,632) and consumed it. `get_approvals` read back an empty list.
- Result: about 0.3785 USDC was read in the recipient wallet on Base by hand after the swap (reported; no screenshot is published and the recipient address is not published here). The vault's wNEAR balance on `wrap.near` read `"0"`, so no refund was left in the vault.
- On chain: the vault emitted `intent_sent`; `wrap.near` logged the 0.1 NEAR deposit and the transfer of 0.1 wNEAR from the vault to the deposit address. All receipts succeeded. `get_stats` read 1 allowed and 0 denied; `spent_in_window` read 0.1025 NEAR (the amount plus the two storage deposits).

## Gas, one measured sample

| Receipt | Burnt gas |
|---|---|
| `spend_intent` transaction | 0.315 Tgas |
| `spend_intent` on the vault | 3.094 Tgas |
| The four-action receipt to `wrap.near` (two `storage_deposit`, `near_deposit`, `ft_transfer`) | 5.998 Tgas |
| Two refund transfers to the agent | 0.223 Tgas each |
| Total | 9.853 Tgas of 150 Tgas prepaid |

The refunds to the agent (0.042629 and 0.10735 NEAR) are the part of the gas reservation that was not burnt. The key allowances fell only by the fees: guardian 0.07 to 0.06977 NEAR, agent 0.31 to 0.30906 NEAR. The gas constants in the contract (120 Tgas minimum prepaid, 10 Tgas per action) were not changed; this is a single run.

## Balance reading

The vault's available balance fell by 0.100134 NEAR. The receipt sent 0.1025 NEAR (0.1 plus two storage deposits of 0.00125). The difference of 0.002366 NEAR is the storage released when the approval record was removed, as in the testnet run. Afterwards `storage_balance_of` for the vault on `wrap.near` read 0.00125 NEAR, so the vault is now registered there. That it was not registered before the swap is inferred from this arithmetic and the receipts; it was not read before.

## First deploy attempt

The first deploy on mainnet was rejected with a link error ("unknown or invalid import") and rolled back; the account held no contract afterwards. Mainnet reported protocol version 86 and testnet 88 when read on the same day. The rejected build used `near-sdk` 5.30.0; the build that deployed uses `near-sdk` 5.28.3. Which single import caused the error was not confirmed on a chain. The source of the contract did not change.

## Limits of this proof

- One swap, one sample. Settlement on Base is shown by the wallet balance, not by a contract callback; the vault does not wait for it.
- The vault is not locked and the guardian link to the owner's route is checked off-chain, as described in [security](security.md) and [ADR 0005](adr/0005-intents-design-and-trust.md).
- The recipient of a route is readable on the chain through `get_routes`.
