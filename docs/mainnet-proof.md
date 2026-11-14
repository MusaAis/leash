# Mainnet proof

Two vaults have run on NEAR mainnet. The current vault is `leash2-vault.near`. The first vault ran one real swap on 2026-11-04. Every transaction below links to the explorer. Every value is read from the command output or the explorer.

## Current vault

- Vault: `leash2-vault.near` (contract 0.3.0, not locked)
- Guardian: `leash2-guardian.near`
- Agent: `leash2-agent.near`
- Owner: the implicit account `839d44f94fb99bb1412c68f2fa8534a8a739a1478d8bb1dc97663d02cca51d09`. Read its keys with `near account list-keys` on mainnet.

| Step | Transaction |
|---|---|
| Create `leash2-vault.near` (2.10 NEAR) | [5oKFVK…1mNg8](https://explorer.near.org/transactions/5oKFVKU1X2MrVdRaXVhuzz7dbpkYdjeBm654bb81mNg8) |
| Create `leash2-guardian.near` (0.08 NEAR) | [HDcGCE…kQDkC](https://explorer.near.org/transactions/HDcGCEaRZCnQLKaJnYALrMMQpxk8RX6gpY17PpxkQDkC) |
| Create `leash2-agent.near` (0.25 NEAR) | [2qWGbf…c83Gj](https://explorer.near.org/transactions/2qWGbfyB9gWYPqAbDeBamMBaVVG2XucMg3G9cfbc83Gj) |
| Deploy contract and call `new` | [CjXvHy…PV8tj](https://explorer.near.org/transactions/CjXvHyMdKdwHGeSLna7dNmqiZiomCZmEaBHeHZAPV8tj) |
| `set_policy`: per-transaction cap 0.15 NEAR, daily cap 0.30 NEAR, one allowlisted account | [ho7wSt…ByvDp](https://explorer.near.org/transactions/ho7wStyxp5uzALEkXL7sEWRaPm8FtYmgs597UXByvDp) |
| `fund` 0.30 NEAR | [7Wr5oV…GNPZo](https://explorer.near.org/transactions/7Wr5oVfKZEp5gCEnKtvKb6SfLFJSoLdnebFHd7NGNPZo) |
| `set_intents_config` (wNEAR `wrap.near`, guardian `leash2-guardian.near`) | [6fUtMA…MD3yQ](https://explorer.near.org/transactions/6fUtMAXvnKXCuUxRBGoCc8qbwXxPYRyEFxzugPNMD3yQ) |
| `set_routes`: one route | [AYwK1M…nRUSg](https://explorer.near.org/transactions/AYwK1MXngwZjuhFNbGKmQGLdbKDWtWBKTN54LKFnRUSg) |

The deployed code is the release artifact of `v0.3.2`: its SHA-256 is `18f112959fc2a6ec4a83489803589c83872c43564e96cd94ac70df1f1113dff0` (see [build](build.md)). `vault-has-code.sh` compares the code hash on chain, `2gMzTHTr2TwtwAaHsXZbKH9nBLi2ZfU9HLMVHG82B8KZ` in base58, with that file.

### Spend, denial and kill-switch proofs

The agent signs each `spend` with its function-call key. The owner signs `pause_all` and `resume_all`. The vault returns `true` for an allowed call and `false` with a reason for a denied one; the reason is in the event on the transaction page.

| Step | Result | Transaction |
|---|---|---|
| `spend` of 0.01 NEAR to the allowlisted owner | `spend_allowed` | [CKSMpu…rPU3P](https://explorer.near.org/transactions/CKSMpudnDdBsEosqZgMNJpt99SJ3tdA9GK287NMrPU3P) |
| `spend` to an account that is not allowlisted | `spend_denied`, `not_allowlisted` | [J3zupz…TXN3o](https://explorer.near.org/transactions/J3zupzmFKKmbBdT7fF7DxV9SxzPE26geE3RSZobTXN3o) |
| `spend` of 0.2 NEAR, above the per-transaction cap | `spend_denied`, `over_per_tx_cap` | [Aw6vsx…sAwV1](https://explorer.near.org/transactions/Aw6vsxFgjGHo5EXLkUCGxhCVJxSkka9QDPUNvt8sAwV1) |
| Owner `pause_all` | `paused_all` | [8SVuyQ…ASz22](https://explorer.near.org/transactions/8SVuyQ2rNatPHy1MTSztLufreWrg1pJxMC6wCQBASz22) |
| `spend` of 0.01 NEAR while paused | `spend_denied`, `all_paused` | [4z6Cns…HJPeB](https://explorer.near.org/transactions/4z6CnsyPrc77muv8wWYCq6NNrD9GcbSrb9n8rjsHJPeB) |
| Owner `resume_all` | `resumed_all` | [2KcPfH…a2z5q](https://explorer.near.org/transactions/2KcPfHrvEpGuLzGNZavLNXmcwa82qF3wRvS2UsZa2z5q) |
| `spend` of 0.01 NEAR after resume | `spend_allowed` | [5jfTue…W2JQb](https://explorer.near.org/transactions/5jfTueRwocR2T1ZhgV4gHxeAoDqCVXoHGYnd5sgW2JQb) |

### Decision chain check

The five `spend` calls above emitted five decision events. `scripts/collect_decisions.py` reads them from the receipts in block order and reads `get_head_hash` from the vault. `scripts/replay_head.py` hashes the lines as printed and compares the result with the head.

| Check | Result |
|---|---|
| Replay of the five events against the head `9c071ee702f9b42522a84eda8368f60b708dc7b822064de14211a1bc2b4bf52e` | `chain OK` |
| The same file with one byte of the first line changed | `chain BROKEN` |

Run it with `python3 scripts/collect_decisions.py https://free.rpc.fastnear.com leash2-vault.near leash2-agent.near <file of transaction ids> <events file> --head`, then `python3 scripts/replay_head.py <events file> <head>`. The chain covers the decisions of `spend` and `spend_intent`. Owner actions are not chained.

## Retired vault

Retired vault: `leash-vault.near` held the first mainnet swap. The account was deleted and its name is now controlled by another key. Its transactions remain on chain.

## First swap, through the retired vault

### Accounts

- Vault: `leash-vault.near` (contract 0.2.0, since deleted)
- Guardian: `leash-guardian.near`. It signs with a function-call key limited to `approve_intent` on the vault.
- Agent: `leash-agent.near`. It signs with a function-call key limited to `spend` and `spend_intent` on the vault.
- Owner: the implicit account `839d44f94fb99bb1412c68f2fa8534a8a739a1478d8bb1dc97663d02cca51d09`. Read its keys with `near account list-keys` on mainnet.
- `make intents-keys` stops when a full-access key file of the guardian or the agent is present on the machine it runs on.

### Transactions

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

### The swap

- Quote: 0.1 NEAR in, 378,371 units of USDC out (6 decimals, 0.378371 USDC), minimum 374,587, estimate 24 seconds. The guardian verified every field before approving.
- Approval: expiry 05:17:07 UTC, 17 minutes; the spend ran at 05:02:58 UTC (block 222,973,632) and consumed it. `get_approvals` read back an empty list.
- Result: 1Click returned status SUCCESS and delivered 0.37853 USDC on Base (quote: 0.378371, minimum 0.374587). The vault's wNEAR balance on `wrap.near` read `"0"`, so no refund was left in the vault.
- On chain: the vault emitted `intent_sent`; `wrap.near` logged the 0.1 NEAR deposit and the transfer of 0.1 wNEAR from the vault to the deposit address. All receipts succeeded. `get_stats` read 1 allowed and 0 denied; `spent_in_window` read 0.1025 NEAR (the amount plus the two storage deposits).

### Gas, one measured sample

| Receipt | Burnt gas |
|---|---|
| `spend_intent` transaction | 0.315 Tgas |
| `spend_intent` on the vault | 3.094 Tgas |
| The four-action receipt to `wrap.near` (two `storage_deposit`, `near_deposit`, `ft_transfer`) | 5.998 Tgas |
| Two refund transfers to the agent | 0.223 Tgas each |
| Total | 9.853 Tgas of 150 Tgas prepaid |

The refunds to the agent (0.042629 and 0.10735 NEAR) are the part of the gas reservation that was not burnt. The key allowances fell only by the fees: guardian 0.07 to 0.06977 NEAR, agent 0.31 to 0.30906 NEAR. The gas constants in the contract (120 Tgas minimum prepaid, 10 Tgas per action) were not changed; this is a single run.

### Balance reading

The vault's available balance fell by 0.100134 NEAR. The receipt sent 0.1025 NEAR (0.1 plus two storage deposits of 0.00125). The difference of 0.002366 NEAR is the storage released when the approval record was removed, as in the testnet run. Afterwards `storage_balance_of` for the vault on `wrap.near` read 0.00125 NEAR, so the vault is now registered there.

### Deployment notes

The first deploy was rejected with a link error ("unknown or invalid import") and left the account without code. The build made with `near-sdk` 5.28.3 deployed. Mainnet read protocol version 86 and testnet 88 on the same day; the rejected build used `near-sdk` 5.30.0.

## Values read from the chain

| Value | Reading | Read on |
|---|---|---|
| Code on `leash2-vault.near` | SHA-256 `18f112959fc2a6ec4a83489803589c83872c43564e96cd94ac70df1f1113dff0` (`vault-has-code.sh`) | 2026-11-13 |
| Head hash after deploy | 64 zeros (`get_head_hash`) | 2026-11-13 |
| Head hash after the five decisions | `9c071ee702f9b42522a84eda8368f60b708dc7b822064de14211a1bc2b4bf52e` (`collect_decisions.py --head`) | 2026-11-13 |
| Keys of the vault | one full-access key (`near account list-keys`) | 2026-11-13 |
| Keys of the guardian | one full-access key and one function-call key for `approve_intent` on the vault, allowance 0.07 NEAR | 2026-11-13 |
| Keys of the agent | one full-access key and one function-call key for `spend` and `spend_intent` on the vault, allowance 0.3073 NEAR | 2026-11-13 |
| Agent policy caps | 0.15 NEAR per transaction, 0.30 NEAR per 24 hours, not paused (`get_policy`) | 2026-11-13 |
| Guardian and wNEAR contract | `leash2-guardian.near` (`get_guardian`), `wrap.near` (`get_wnear`) | 2026-11-13 |
| Routes | one (`get_routes`) | 2026-11-13 |

## Limits of this proof

- One swap, one gas sample. The vault does not wait for settlement; 1Click returned status SUCCESS for this swap.
- The current vault is not locked and the guardian link to the owner's route is checked off-chain, as described in [security](security.md) and [ADR 0005](adr/0005-intents-design-and-trust.md).
- The recipient of a route is readable on the chain through `get_routes`.
