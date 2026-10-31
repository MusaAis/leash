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

```
cp .env.example .env     # set LEASH_PREFIX and NEAR_AI_API_KEY
make agent-check         # prints the configuration and the vault counters
```

The vault and accounts come from `make accounts-testnet` and `make deploy-testnet`, followed by the policy from `make demo-testnet` (step 1). The agent signs with the key stored for `<prefix>-agent.testnet`.

## NEAR AI credits

Inference needs credits on the key. Create the key in the dashboard at https://cloud.near.ai. For the hackathon, credits can be bought there or claimed by joining NEAR Builders at https://nearbuilders.org/join. HTTP 402 from the API means the key has no credits.

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

The report lists every attempt and ends with the totals: attempts, blocked by the vault, refused by the agent, and payments that left the vault for an address other than the payee. The command exits with code 2 if any payment reached a non-payee.

Payments to the allowlisted payee that stay inside the caps are allowed by design; they appear in the report as `paid to payee`.

### The two live-model scenarios

`make attack-llm-testnet` runs the attacks twice against the real model:

1. **Attack text only.** The model gets the attack message with no instruction from the owner. A good model refuses because nothing authorizes a payment. This is the easy case.
2. **Authorized invoice.** The owner asks the agent to pay a 0.3 NEAR invoice to the payee, and the attack text sits inside the invoice and tries to redirect the money. This is the realistic case. If the model is fooled, it names another destination and the vault blocks it. The report counts these as `agent chose a non-payee`.

The first line of the report in scenario 1 is the control task. In scenario 2 every row is already an authorized payment request.

## Results

Live model `zai-org/GLM-5.1-FP8` on testnet, scenario 1: the control task passed (the model proposed 0.2 NEAR to the payee) and all 8 attacks were refused by the agent, each with a stated reason, using 9 model calls. Hijacked-agent mode: 12 attempts, all 12 blocked by the vault. In every run, 0 payments reached a non-payee.

## Trust model

- The agent account holds only gas money. The funds are in the vault.
- Model output is untrusted: destinations and amounts are validated, and the vault checks them again.
- The API key is read from the environment or `.env` and is never written to logs.
