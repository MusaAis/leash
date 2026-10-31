# Deployment guide

## Prerequisites

- Rust (stable) with the `wasm32-unknown-unknown` target
- [cargo-near](https://github.com/near/cargo-near) and [near-cli-rs](https://github.com/near/near-cli-rs)

## Testnet

Choose a unique prefix. All account names derive from it.

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

`make deploy-testnet` deploys and initializes in a single transaction signed by the vault account. This is required, because `new` can only be called by the contract account itself.

Every command prints a transaction hash. Open it in a block explorer to see the emitted events.

## Before real funds

Remove every full-access key from the vault account, so no one can bypass the policy or replace the contract:

```
make lock-vault-testnet
```

The script lists the keys, asks which one to delete, and requires you to type the account name. Afterwards, check that the account has no full-access keys on an explorer. Run the demo first: deleting the last key is irreversible.

## Verification checklist

- `get_owner` returns the intended owner.
- The vault account has no full-access keys.
- `get_stats` increases after a blocked attempt.
