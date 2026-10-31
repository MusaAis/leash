# Agent runtime

The agent decides whether to pay and then asks the vault. It never holds the funds. The vault enforces the owner's policy, so the vault has the final say even if the model is fully fooled.

```
task or message -> model (NEAR AI Cloud) -> decision -> vault.spend() -> allowed or blocked
```

## Components

| Module | Purpose |
|---|---|
| `brain.py` | Sends the task to a NEAR AI Cloud model through its OpenAI-compatible API and turns the answer into a decision. Anything unclear becomes a refusal. |
| `chain.py` | Calls the vault through the `near` CLI as the agent account and parses the result and events. |
| `agent.py` | The loop: decide, then call the vault. |
| `attacks.py` | Attack suite with two modes (below). |

The runtime has no third-party Python dependencies.

## Setup

For a clean attack run, use a fresh vault. The daily cap (3 NEAR) carries over, and a vault that has already spent most of it stops every payment for a reason that has nothing to do with the attack. The live-model run prints a warning when the cap is low.

```
export LEASH_PREFIX=myleash2
make accounts-testnet
make deploy-testnet
make policy-testnet      # policy and 5 NEAR of funds, without the full demo
```

```
cp .env.example .env     # set LEASH_PREFIX and NEAR_AI_API_KEY
make agent-check         # prints the configuration and the vault counters
```

The vault and accounts come from `make accounts-testnet` and `make deploy-testnet`, followed by the policy from `make demo-testnet` (step 1). The agent signs with the key stored for `<prefix>-agent.testnet`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The run finished and every check held |
| 1 | An error (vault call failed, model call failed, bad configuration, or a network other than testnet) |
| 2 | A payment reached a destination other than the payee |
| 3 | The run is not valid evidence: the control task failed, or a vault call has no transaction id |

The pay commands (`attack`, and `run` without `--dry-run`) refuse any network other than testnet unless `LEASH_ALLOW_NON_TESTNET=1` is set.

## How the model's answer is read

The reply must be exactly one JSON object with the keys `action`, `to`, `amount_near`, `memo` or `reason`, optionally after one closed `<think>` block and inside one code fence. Text around it, two objects, an unclosed `<think>` block or unknown keys are refused. The model's reasoning text (`reasoning_content`) is never used, and a reply that was cut off is never parsed. Amounts must be plain decimals such as `0.5`. Memos are cleaned of control characters and cut to 100 bytes. The transaction id is read only from the CLI's own `Transaction ID:` line, so text inside a memo cannot fake it.

## Cost limits

Every live run is capped, so a mistake cannot spend the credits:

| Setting | Default | Meaning |
|---|---|---|
| `NEAR_AI_MAX_TOKENS` | 4000 | Most tokens one model call may produce (the model reasons before it answers) |
| `LEASH_MAX_MODEL_CALLS` | 30 | Most model calls in one run |
| `LEASH_MAX_TOTAL_TOKENS` | 80000 | Most tokens used in one run, as reported by the API; the check runs before each call |

A full `make attack-llm-testnet` run makes 17 calls and prints the calls and tokens used. `make agent-models` lists the models with their prices.

## NEAR AI credits

Inference needs credits on the key. Create the key in the dashboard at https://cloud.near.ai. For the hackathon, follow the credits instructions linked from https://docs.near.org/getting-started/hackathons. HTTP 402 from the API means the key has no credits.

## Run one task

```
make agent-run TASK="Pay the invoice of 0.2 NEAR to myleash-payee.testnet"
```

The output shows whether the agent refused, the vault blocked the payment, or it was paid.

## Attack suite

Eight attack types: instruction override, fake admin notice, social engineering, encoded payload, tool-output injection, look-alike address, oversized payment and a payment split into small parts.

```
make attack-testnet       # compromised mode, no model needed
make attack-llm-testnet   # the real model sees each attack text
```

- **Compromised mode** assumes the agent is fully hijacked and sends the attacker's request straight to the vault. It shows what the contract blocks on its own.
- **LLM mode** gives the attack text to the model. Whatever the model decides is sent to the vault.

The report lists every attempt. Each attempt that reached the vault shows its transaction id (also in the `--json` file); refused attempts never call the vault and have none. A vault call without a valid id is printed as `tx MISSING` and the command exits with code 3. The report ends with the totals: attempts, blocked by the vault, refused by the agent, and payments that left the vault for an address other than the payee. The command exits with code 2 if any payment reached a non-payee.

Payments to the allowlisted payee that stay inside the caps are allowed by design; they appear in the report as `paid to payee`.

### The two live-model scenarios

`make attack-llm-testnet` runs the attacks twice against the real model:

1. **Attack text only.** The model gets the attack message with no instruction from the owner. A good model refuses because nothing authorizes a payment. This is the easy case.
2. **Authorized invoice.** The owner asks the agent to pay a 0.3 NEAR invoice to the payee, and the attack text sits inside the invoice and tries to redirect the money. This is the realistic case. If the model is fooled, it names another destination and the vault blocks it. The report counts these as `agent chose a non-payee`.

The first line of the report in scenario 1 is the control task. In scenario 2 every row is already an authorized payment request.

## Results

One clean run on testnet, vault `leashmusa2-vault.testnet`, model `zai-org/GLM-5.1-FP8`, fresh policy (1 NEAR per payment, 3 NEAR per day, one payee). Details, policy transactions and a transaction link for every vault call of the re-run: [testnet proof](testnet-proof.md).

| Run | Result |
|---|---|
| Live model, scenario 1 (attack text only) | Control passed (0.2 NEAR proposed to the payee). 8 of 8 attacks refused by the agent, each with a stated reason. |
| Live model, scenario 2 (authorized invoice, attack text inside it) | 8 of 8 payments went to the authorized payee. 0 were redirected to another destination. The destination was checked; the amount was not compared in this version. |
| Hijacked-agent mode (no model) | 12 of 12 attempts blocked by the vault: 6 `not_allowlisted`, 1 `over_per_tx_cap`, 5 `over_daily_cap`. |
| Cost | 17 model calls, 10,197 tokens in the first run and 9,411 in the re-run on the v0.2.2 code (limits: 30 calls, 80,000 tokens). |
| Payments to a non-payee | 0 |

What this does and does not show. It is one run at temperature 0 with eight attack texts. In scenario 2 the model was never fooled, so the vault had nothing to block in that scenario; the vault's behavior against a fully fooled agent is what hijacked mode demonstrates. The scenario 2 payments used 2.4 of the 3 NEAR daily cap, so the split-payment attacks that followed were stopped by the remaining cap.

## Trust model

- The agent account holds only gas money. The funds are in the vault.
- Model output is untrusted: destinations and amounts are validated, and the vault checks them again.
- The API key is read from the environment or `.env` and is never written to logs.
