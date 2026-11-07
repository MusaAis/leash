# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub Security Advisories on this repository: open the Security tab and choose "Report a vulnerability". Do not open public issues for security problems.

## Scope

- **Contract** (`contract/`): the vault, its access control, caps, approvals and events.
- **Agent and guardian** (`agent/`): quote verification, key handling and the intents client.
- **Scripts** (`scripts/`, `Makefile`): anything that handles keys, accounts or the network choice.

Issues in third-party services (1Click, `wrap.near`, NEAR AI Cloud) belong with their maintainers.

## What to include

- The commit or tag, and the network.
- The steps that reproduce the problem, and what you expected to happen.
- Transaction ids or log lines, with keys and seed phrases removed.

## Response

The maintainer aims to acknowledge reports within 72 hours. Fixes are made on the latest release, as a patch release, and listed in the [changelog](CHANGELOG.md).

The threat model, the guarantees and the limits are in [docs/security.md](docs/security.md).
