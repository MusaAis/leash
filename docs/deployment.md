# Deployment guide

## Prerequisites

- Rust (stable) with the `wasm32-unknown-unknown` target
- [cargo-near](https://github.com/near/cargo-near) and [near-cli-rs](https://github.com/near/near-cli-rs)

## Testnet

Choose a unique prefix. All account names derive from it. Use a fresh prefix for every run of the demo, because the daily cap carries over between runs on the same vault.

```
export LEASH_PREFIX=myleash
make accounts-testnet
make deploy-testnet
make demo-testnet
```

| Account | Role |
|---|---|
| `<prefix>-vault.testnet` | Holds the contract and the funds |
| `<prefix>-owner.testnet` | Sets policy, funds, pauses |
| `<prefix>-agent.testnet` | Calls `spend` |
| `<prefix>-payee.testnet` | Allowed destination in the demo |
| `<prefix>-attacker.testnet` | Destination used by the blocked attack |
| `<prefix>-ghost.testnet` | Allowlisted but never created, to test a failed transfer |

`make deploy-testnet` deploys and initializes in a single transaction signed by the vault account. This is required, because `new` can only be called by the contract account itself.

Every command prints a transaction hash. Open it in a block explorer to see the emitted events.

## Policy only

`make policy-testnet` sets the agent policy (1 NEAR per payment, 3 NEAR per day, one payee) and funds the vault, without running the full demo. It removes any earlier policy first, so the agent's daily counter starts at zero and an old test vault can be reused. `LEASH_FUND="2 NEAR"` changes the amount (default 5 NEAR); `LEASH_FUND=0` skips funding. Use it on test vaults only.

## Before real funds

Remove every full-access key from the vault account, so no one can bypass the policy or replace the contract:

```
make lock-vault-testnet
```

The script lists the keys, asks which one to delete, and requires you to type the account name. Afterwards, check that the account has no full-access keys on an explorer. Run the demo first: deleting the last key is irreversible. Lock only the final vault, and rehearse on a separate prefix.

Locking removes upgrades, not the owner's exit: `withdraw` stays available to the owner and is not blocked by pause. After locking, a bug in the contract cannot be patched in place.

## Verification checklist

- `get_owner` returns the intended owner.
- The vault account has no full-access keys (`make lock-vault-testnet` ends with `Full-access keys remaining: 0`).
- `get_stats` increases after a blocked attempt.
