# NEAR Intents quotes (1Click)

What exists in this release: a client for the 1Click API and a guardian that builds a quote request from fixed rules and checks the answer. It asks for **dry quotes only**. No deposit is made, no funds move, and the vault does not call Intents yet.

## Run it

Needs `.env` with `LEASH_ROUTE_RECIPIENT` (a wallet you control on the destination chain) and `LEASH_DRY_REFUND_TO` (an existing NEAR account). See `.env.example`. A repeated key in `.env` is an error.

```
make intents-tokens SYMBOL=USDC CHAIN=base   # prints one row: the USDC asset id on Base
make intents-dry AMOUNT=0.1                  # prints the request and the raw answer, then a verified: line
```

Expected: `intents-dry` ends with `verified: amountIn=... amountOut=... minAmountOut=... timeEstimate=...`. The recipient is hidden in the printout. If the answer does not match the request, the command prints `REJECTED by the guardian: <reason>` and the answer, and exits with 1.

## What the guardian does

- Builds the request itself: swap NEAR (`nep141:wrap.near`) for one destination asset, to the owner's recipient, refund to the owner's account, deadline within 30 minutes, slippage at most 3%. The agent supplies only a route id and an amount.
- Checks the answer field by field: recipient, refund account, both assets, amount and `amountIn` must equal the request; `minAmountOut` must respect the slippage; no extra fee or message fields.
- Sends requests only to `1click.chaindefuser.com` over https, with no redirects and a size cap.
- Keeps a private log (`~/.leash/guardian-log.jsonl`, mode 0600) with every answer and its signature. The quote signature is saved, not verified.

## Recorded results (mainnet, dry quotes, 2026-11-01)

- A request with the default Python user agent got HTTP 403 (Cloudflare error 1010). The client sends `User-Agent: leash-agent/0.3 (+https://github.com/MusaAis/leash)`.
- The token list holds exactly one USDC on Base: `nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near` (6 decimals).
- Without an API key, 1Click adds one fee entry to the echoed request: `fee: 20` (20 bps, 0.2%) to the account `5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd`. It was identical in every answer. The guardian accepts exactly this entry and rejects any other `appFees` value. The docs (docs.near-intents.org, fees page) state a 0.2% fee without an API key; they do not name this account, so it is **inferred** to belong to 1Click. Effect if wrong: at most 0.2% of a swap. If 1Click changes it, the guardian refuses.
- In the 0.5 NEAR answer: `refundFee` 0, `withdrawFee` 2400 (0.0024 USDC). The output at small sizes is consistent with a fixed fee of that size; it was read from the 0.5 answer only.

| NEAR in | USDC out (6 decimals) | Estimate (s) |
|---|---|---|
| 0.5 | 2184717 | 22 |
| 0.25 | 1089424 | 24 |
| 0.1 | 434320 | 24 |
| 0.05 | 215959 | 24 |
| 0.02 | 84942 | 24 |
| 0.01 | 41240 | 24 |
| 0.005 | 19424 | 24 |
| 0.002 | 6328 | 24 |
| 0.001 | 1962 | 24 |

Prices move between calls (two answers for 0.5 NEAR gave 2177761 and 2184717). Every size passed the guardian's checks.

## Not done in this release

- No deposit and no real swap. 1Click gave no minimum in dry quotes down to 0.001 NEAR; a minimum may still apply at deposit time, which is untested.
- The vault does not execute payments through Intents.
- The quote signature is not verified.
