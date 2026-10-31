# Testnet proof

## Run 1

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

## Run 2: asserted demo

`make demo-testnet` with a check per step, contract version 0.1.2. Result: **12 passed, 0 failed**.

Vault: `leashmusa2-vault.testnet` · Owner: `leashmusa2-owner.testnet` · Agent: `leashmusa2-agent.testnet` · Payee: `leashmusa2-payee.testnet`

| Step | Action | Result | Transaction |
|---|---|---|---|
| 4 | Attack: pay an address that is not allowlisted | `false`, `not_allowlisted` | [FgyFw9…Yk](https://explorer.testnet.near.org/transactions/FgyFw97PKHYWhdTvKWwiwhFBFdVDjzSaFqUAWX61yBYk) |
| 5 | Attack: pay 2 NEAR in one payment | `false`, `over_per_tx_cap` | [GDJdRo…nAt](https://explorer.testnet.near.org/transactions/GDJdRo1muhyinBtaUtgXquuaPuvVXrgmEudmB2jrjnAt) |
| 6 | Split payment, part 1 (1 NEAR) | `true` | [4RDM2N…DiT](https://explorer.testnet.near.org/transactions/4RDM2NxRrBf5AKJ245FxWhf4RfTYR6BEcDic6EfAtDiT) |
| 6 | Split payment, part 2 (1 NEAR) | `true` | [2kTXQ6…97q](https://explorer.testnet.near.org/transactions/2kTXQ6ESyxBM7c5ykyyXZFbFqJF1GrtiBY5VgBpBK97q) |
| 6 | Split payment, part 3 (1 NEAR) | `false`, `over_daily_cap` | [H9nv6A…p8R](https://explorer.testnet.near.org/transactions/H9nv6AJeMiBwveaxrTQ4wMaHWk6TjkxoxFJCHGgdAp8R) |
| 7 | Owner pauses the agent | `agent_paused` | [Df9qBY…ECd](https://explorer.testnet.near.org/transactions/Df9qBY2UyPhP2LxpJ595JQ1FFE4wNryiEWiXJBN16ECd) |
| 7 | Payment while the agent is paused | `false`, `agent_paused` | [AUku96…sPS4](https://explorer.testnet.near.org/transactions/AUku965KYrrZQRer85SsaXseJJ7K1uZ2VAMT35ibPsS4) |
| 7 | Owner resumes the agent | `agent_resumed` | [Dm2xaf…yMq](https://explorer.testnet.near.org/transactions/Dm2xafkiLk19Rfj6iC7ZUaK1f4RP9trqZxnknweoEyMq) |
| 8 | Kill switch | `paused_all` | [7Y89a4…ddJ](https://explorer.testnet.near.org/transactions/7Y89a4hcx99di8ybfV7o97H1WEnDzczjTFRnZWmGMddJ) |
| 8 | Payment while paused | `false`, `all_paused` | [7Bk5Yv…9RkN](https://explorer.testnet.near.org/transactions/7Bk5YvAJEns8v8zSSpzG636X6yD3GnCJug5vrDtH9RkN) |
| 8 | Owner resumes | `resumed_all` | [F3RB3Y…yvTz](https://explorer.testnet.near.org/transactions/F3RB3YNPcL5Hx56u7X1WNrizS5yDyp4SauiDcoshvyTz) |
| 9 | Payment after resume (0.1 NEAR) | `true` | [24mDzG…AsF](https://explorer.testnet.near.org/transactions/24mDzGH72addMgVoVPt1uhcV1mzcPHWhMtNCjpguWAsF) |
| 10 | Payment to an allowlisted account that does not exist (0.2 NEAR) | `true`; vault balance restored after the refund; daily cap stays consumed | [ACtMJa…nfR](https://explorer.testnet.near.org/transactions/ACtMJaLqURCX1eUzxdHkDsYTFtrbeqZ7EKxiKAnMWnfR) |

Counters after the run: 5 allowed, 5 denied. Spent in the daily window: 2.8 NEAR of 3 NEAR.

## Attack suite, hijacked-agent mode

`make attack-testnet` against the same vault: 12 attempts, 12 blocked by the vault, 0 paid to a non-payee. Blocked reasons: `not_allowlisted` (look-alike address and five attacker-address attacks), `over_per_tx_cap` (oversized payment) and `over_daily_cap` (five split payments).
