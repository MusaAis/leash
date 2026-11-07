# NEAR Intents quotes (1Click)

Contents: a client for the 1Click API, a guardian that builds a quote request from fixed rules and checks the answer, and the commands that carry an approved quote through the vault. Dry quotes only move no funds. Real payments go through the vault: the guardian approves one quote at a time on the vault and the agent then calls `spend_intent` (see [mainnet proof](mainnet-proof.md) for one real swap).

## Run it

Needs `.env` with `LEASH_ROUTE_RECIPIENT` (a wallet you control on the destination chain) and `LEASH_DRY_REFUND_TO` (an existing NEAR account). See `.env.example`. A repeated key in `.env` is an error.

```
make intents-tokens SYMBOL=USDC CHAIN=base   # prints one row: the USDC asset id on Base
make intents-dry AMOUNT=0.1                  # prints the request and the raw answer, then a verified: line
```

Expected: `intents-dry` ends with `verified: amountIn=... amountOut=... minAmountOut=... timeEstimate=...`. The recipient is hidden in the printout. If the answer does not match the request, the command prints `REJECTED by the guardian: <reason>` and the answer, and exits with 1.

## What the guardian does

- Builds the request itself: swap NEAR (`nep141:wrap.near`) for one destination asset, to the owner's recipient, deadline within 30 minutes, slippage at most 3%. The refund goes to the vault account; a dry quote uses the account in `LEASH_DRY_REFUND_TO` instead. The agent supplies only a route id and an amount.
- Checks the answer field by field: recipient, refund account, both assets, amount and `amountIn` must equal the request; `minAmountOut` must respect the slippage; no extra fee or message fields. The quote's deadline must not be earlier than the requested one and at most 4 days later.
- Sends requests only to `1click.chaindefuser.com` over https, with no redirects and a size cap.
- Keeps a private log (`~/.leash/guardian-log.jsonl`, mode 0600, tightened on every write) with every answer and its signature. The quote signature is saved, not verified.
- Counts every call to 1Click, rejected or failed ones too: at most 10 a minute and 60 an hour, and 6 real quotes an hour with one open at a time. A lock file keeps two processes from passing the same check.
- Prints nothing private: the recipient and the refund account are hidden in every printed line, in any letter case, error messages included.

## Recorded results (live 1Click mainnet API, dry quotes)

- A request with the default Python user agent got HTTP 403 (Cloudflare error 1010). The client sends `User-Agent: leash-agent/0.3 (+https://github.com/MusaAis/leash)`.
- The token list holds exactly one USDC on Base: `nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near` (6 decimals).
- Without an API key, 1Click adds one fee entry to the echoed request: `fee: 20` (20 bps, 0.2%) to the account `5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd`. It was identical in every answer. The guardian accepts exactly this entry and rejects any other `appFees` value. The docs (docs.near-intents.org, fees page) state a 0.2% fee without an API key and do not name this account. The effect is at most 0.2% of a swap. If 1Click changes it, the guardian refuses.
- In the 0.5 NEAR answer: `refundFee` 0 and `withdrawFee` 2400 (0.0024 USDC). It was not read at other sizes.

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

## Two real quotes, never paid (0.1 NEAR each)

Observations from two answers, not documented behavior of the service.

- Both answers hold a deposit address that is a 64-character lowercase hex implicit account. The guardian now refuses any other shape for a wet quote (fail closed). The rule is relaxed only by a decision, after another real sample.
- In both, the quote's `deadline` is exactly 3 days after the deadline in the request (20 minutes), so the address stays active for 3 days. The echoed `quoteRequest.deadline` equalled the asked one; the guardian refuses a different one, and logs both. The asked deadline is the one the guardian uses for its "one open quote" rule.
- The platform fee entry in both wet answers has one more key, `limitOrderId`, set to `null`; a dry answer for 0.1 NEAR has the entry without that key. The guardian accepts both, and `limitOrderId` only as `null`. The dry answer echoed the asked deadline too.
- 0.1 NEAR gave 0.425652 USDC (minimum 0.421395) in the first and 0.421467 USDC (minimum 0.417252) in the second; estimate 24 seconds, `withdrawFee` 2400 in both. Nothing was deposited. The probe stops after 3 wet quotes in the log.

## Preparing real payments (written and unit-tested)

```
make intents-wet-probe        # one real quote of 0.1 NEAR: prints the deposit address, its format and the expiry
make wrap-storage             # read-only: wrap.near storage bounds and the storage balance of the probe's address (ACCOUNT=x.near adds more)
```

The probe never deposits. Never send funds to the address it prints. The storage check sends no transaction and holds no key; it reads `LEASH_RPC_URL` (default: a public node from the NEAR docs list). The storage deposit of `wrap.near` is 0.00125 NEAR (minimum and maximum are the same). The storage balance of both deposit addresses above was `null` (not registered), so a fresh deposit address is not registered there. The vault's payment batch registers the deposit account first (see [mainnet proof](mainnet-proof.md)).

## Routes are stored on the vault

The owner stores each route (destination asset and recipient) on the vault with `set_routes`. Routes are readable by anyone through `get_routes`. `intents-quote` reads the route of the agent from the vault and builds the quote from it; a value in `.env` can only confirm it, and a difference is refused. A missing route is refused. `intents-dry` and `intents-wet-probe` still read the route from `.env`. The recipient is never printed.

What the guardian checks off-chain and what the contract enforces are set out in the [security model](security.md) and in [ADR 0005](adr/0005-intents-design-and-trust.md).

## Call path through the vault (unit-tested; run on testnet and once on mainnet, see [mainnet proof](mainnet-proof.md))

Each command builds one `near` call, checks the network guard first (mainnet needs `LEASH_ALLOW_MAINNET=1` in the process environment), prints the plan, and sends only with `YES=1` (any other value, including `YES=0`, only prints the plan). Private values are scrubbed from every printed line.

```
make intents-keys                                  # testnet: function-call keys for the guardian and the agent (plan; YES=1 sends; ASIDE=1 moves full-access key files off the machine). Mainnet: read-only check
make intents-config  CONFIRM_VAULT=<vault> YES=1   # owner: set the wNEAR contract and the guardian (1 yocto; revokes approvals)
make intents-routes  CONFIRM_VAULT=<vault> YES=1   # owner: set route 0 from LEASH_ROUTE_DEST_ASSET and LEASH_ROUTE_RECIPIENT
make intents-quote   SWAP_AMOUNT=0.1 YES=1         # guardian: one real quote built from the route stored on the vault, verified and saved in the private log (mainnet only)
make intents-approve SWAP_AMOUNT=0.1 YES=1         # guardian key: one single-use approval from the saved quote
make intents-spend   SWAP_AMOUNT=0.1 YES=1         # agent key: spend_intent with the approved deposit account
make intents-status                                # read-only: approvals, stats, policy counters, vault balance
```

- **Owner commands** (`intents-config`, `intents-routes`) need the vault name typed (`CONFIRM_VAULT`) to send. The owner key stays on the owner's own machine; no command here creates or keeps it elsewhere.
- **Owner account.** By default the owner is `<prefix>-owner.<network suffix>`. `LEASH_OWNER` sets another owner account, for example a 64-character implicit account; the vault, guardian and agent names keep following the prefix.
- **Outside testnet** the deposit account and the quote hash come only from the guardian's saved quote. `intents-approve` refuses a typed account or hash, a quote older than 30 minutes or past its deadline, a quote for another amount, a quote that was already approved, and more than 6 approvals in an hour. It reads the vault state again first and refuses a payment the vault would deny. The approval lasts until the earlier of the quote deadline and 29 minutes (the contract allows 30); the expiry is printed in UTC. `intents-spend` takes the deposit account from the last approval in the log and refuses a typed one. On testnet, where 1Click has no testnet, both take `DEPOSIT=<account>` and approve uses a fixed test hash.
- **The plan** shows network, signer, method, gas, route, deposit account, amount in NEAR and the approval expiry, and the agent's caps and what is already used. The route recipient is never printed. If the caps cannot be read, nothing is sent outside testnet.
- **The result line** says `ALLOWED: batch scheduled`, `DENIED: <reason>` or `FAILED: <message>` for `spend_intent`, and whether the expected event was seen for the other methods. It is not proof of a swap: the swap itself is shown by the wNEAR receipts and the vault balance.
- **Keys.** On testnet `make intents-keys YES=1` adds a function-call key to the guardian account (receiver: the vault, method `approve_intent`, allowance 0.07 NEAR) and to the agent account (methods `spend` and `spend_intent`, allowance 0.31 NEAR), then reads every key of both accounts back from the RPC and prints its methods and allowance; without `YES=1` it prints the plan and the keys now on the accounts. `ASIDE=1` then moves the key files of the full-access keys into a folder beside the keychain, so that only function-call keys remain on the machine; nothing is deleted and the full-access keys stay on the chain. On mainnet the command never adds, moves or deletes a key: it checks that this machine holds function-call keys as planned, that `<account>.json` (the file the CLI opens first) holds the function-call key and has mode 600, and that no full-access key file is present, and that no full-access key is on either account unless its public key is listed in `LEASH_KNOWN_FULL_ACCESS_KEYS` (public keys only, comma separated); any other result exits 1. It refuses to run if an owner key file is on the machine. The node reserves about 0.001 NEAR per prepaid Tgas plus 0.001 for each call and refuses a call whose reserved cost is above the key's allowance or the account balance, so the agent account needs at least 0.16 NEAR available for one `spend_intent` at 150 Tgas. The key is created with `autogenerate-new-keypair --signature-scheme ed25519 save-to-legacy-keychain`, so the CLI asks no question. With `ASIDE=1` the function-call key file is also copied to `<account>.json`, the file the CLI opens first, so the CLI signs with the function-call key once the full-access key file is gone.
- **Balance around a spend.** `intents-spend` with `YES=1` reads the vault's available balance first and saves it in the private log; `intents-status` prints it next to the current balance and the difference. Other transactions in between also change the balance.
- **Order of the log.** Outside testnet the private log entries for an approval or a spend are written right before the transaction is handed to the CLI, after every refusal. A send the CLI reports as failed is logged as failed and does not count as an approval or a spend; the CLI error does not prove that the transaction did not land, so `make intents-status` is run before a retry. Before an approval the chain is asked for the agent's approvals too, and an approval for the same deposit account refuses a second one.
- **Repeated spend.** If an earlier spend for the same approval was already sent, the screen says so and asks for `make intents-status` first; the spend is not refused, because a spend that failed before it counted anything may be retried.
- `spend_intent` has its own gas setting, `LEASH_INTENT_GAS_TGAS`: default 150, refused below 120 or above 300. The 30 Tgas default stays for `spend`. The contract panics early below 120.
- The node refuses a call whose reserved cost (about 0.001 NEAR per prepaid Tgas plus 0.001: 0.031 NEAR at 30 Tgas, 0.151 NEAR at 150 Tgas on testnet) is above the key allowance, so each account must be funded above that cost (mainnet plan: guardian 0.08 NEAR, agent 0.25 NEAR). The allowance is the most gas a leaked key can burn. The unused part of the reservation returns to the allowance: after one `approve_intent` and one `spend_intent` on testnet the allowances fell by 0.00023 NEAR (guardian) and 0.00099 NEAR (agent), equal to the transaction fees shown by the explorer. The explorer shows gas use of 2.539 Tgas for `approve_intent` (30 prepaid) and 10.556 Tgas for `spend_intent` (150 prepaid, the whole receipt tree, one sample on `wrap.testnet`); a receipt-by-receipt reading was not made, and nothing here is measured on mainnet. The contract's gas constants (120 Tgas minimum, 10 Tgas per action) are unchanged; the one mainnet sample is in the [mainnet proof](mainnet-proof.md).
- While `intents-routes` runs, the recipient is an argument of the `near` process and is visible in the process list of that machine. It is public on the chain through `get_routes` anyway; run owner commands only on a machine that only the owner uses.
- The loss through a swap is bounded by the daily cap per fixed 24-hour window; across a window boundary, up to twice the daily cap.

## Limits

- One real swap through the vault has been done on mainnet (0.1 NEAR to USDC on Base, see [mainnet proof](mainnet-proof.md)). 1Click gave no minimum in dry quotes down to 0.001 NEAR and the 0.1 NEAR deposit went through (1Click returned status SUCCESS and delivered 0.37853 USDC on Base); no swap below 0.1 NEAR has been paid, so a minimum at deposit time for smaller sizes is not known.
- The quote signature is not verified.
- **Testnet run of the success case** (function-call keys only on the machine, full-access key files set aside): `approve_intent` and `spend_intent` for 0.1 NEAR to a test deposit account were both accepted, the vault emitted `intent_approved` and `intent_sent`, and `wrap.testnet` logged the deposit and the transfer. The counter grew by 0.1025 NEAR (amount plus both storage deposits). The vault's available balance fell by 0.09891 NEAR: 0.1 for the swap, 0.00125 for registering the deposit account (the vault was already registered and its deposit was refunded), minus 0.00234 of storage released when the approval was consumed. Transactions: `approve_intent` 4FKPhfKMmADoMkdCEUy43EurmbcSKCRVLCd3JfjDEh2s, `spend_intent` 344BekmvMeNuW8yA2yrEFyxT9WgRkQSBzNjXTRh4aj3F.

## Dates of the recorded reads

| Record | Date |
|---|---|
| Dry quotes | 2026-11-01 |
| Two real quotes, never paid | 2026-11-01 |
| Storage deposit of `wrap.near` | 2026-11-01 |
