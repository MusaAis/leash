# Testnet proof

A scripted run of `make demo-testnet` on NEAR testnet, contract version 0.1.2. Every row links to a transaction whose logs contain the emitted `EVENT_JSON` event.

Vault: `leashmusa-vault.testnet` · Owner: `leashmusa-owner.testnet` · Agent: `leashmusa-agent.testnet` · Payee: `leashmusa-payee.testnet`

| Step | Action | Result | Transaction |
|---|---|---|---|
| Deploy | Deploy and initialize in one transaction | owner stored on-chain | [7LtB6p…MUF4Z](https://explorer.testnet.near.org/transactions/7LtB6petyiVVVywwMkFThvbsim7LeBJZmLH9vkGMUF4Z) |
| 1 | Owner sets policy (1 NEAR per payment, 3 NEAR per day, one payee) | `policy_set` | [8xGRXV…iay4Vu](https://explorer.testnet.near.org/transactions/8xGRXVd6BirFJDEsRWPNEoaoQJnEgQAwJTQ3vsiay4Vu) |
| 2 | Owner funds the vault with 5 NEAR | `funded` | [9tYCj5…EaxyW](https://explorer.testnet.near.org/transactions/9tYCj5FUx8PBF5ATvSUtnPyZFfut5e2xFJJr4mgEaxyW) |
| 3 | Agent pays the allowed payee 0.5 NEAR | `true`, `spend_allowed` | [8biwos…E9BA](https://explorer.testnet.near.org/transactions/8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA) |
| 4 | Attack: pay an address that is not allowlisted | `false`, `not_allowlisted` | [8xbaUv…StHf](https://explorer.testnet.near.org/transactions/8xbaUvmVhx9QVXSXzM9RwHRc2S8gPfNC8GDgTFG3StHf) |
| 5 | Attack: pay 2 NEAR in one payment | `false`, `over_per_tx_cap` | [AkXA2g…hSgz](https://explorer.testnet.near.org/transactions/AkXA2gMkMYmMdRn4Azhc4xD1jg2bSQE26dP73s9EhSgz) |
| 6 | Owner pulls the kill switch | `paused_all` | [6KnCC7…rCsp](https://explorer.testnet.near.org/transactions/6KnCC795Xj6dg7N5oBhYxUjFT2o4wVnBNL8yHBArCsp) |
| 7 | Agent pays while paused | `false`, `all_paused` | [BTwPZR…cAyu](https://explorer.testnet.near.org/transactions/BTwPZRYuWioj9bBY257w1FKrFfSVZQ2UHAo8YrJFcAyu) |
| 8 | Owner resumes | `resumed_all` | [7JWXdK…Wuj](https://explorer.testnet.near.org/transactions/7JWXdKSX95DAWccCTqz9jU28teAWUg5dSimJBh6oHWuj) |
| 8 | Agent pays again | `true`, `spend_allowed` | [CBshS2…B4b](https://explorer.testnet.near.org/transactions/CBshS29P4YyRoxHn2Zm3EhysVkTZHXwX97DKD2EZiB4b) |

Final counters: `get_stats` returned 2 allowed and 3 denied.
