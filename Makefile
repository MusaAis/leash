.PHONY: test build fmt lint check accounts-testnet deploy-testnet demo-testnet lock-vault-testnet

test:
	cd contract && cargo test

build:
	cd contract && cargo near build non-reproducible-wasm

fmt:
	cd contract && cargo fmt

lint:
	cd contract && cargo fmt --check && cargo clippy --all-targets -- -D warnings

check: lint test

accounts-testnet:
	scripts/accounts-testnet.sh

deploy-testnet: build
	scripts/deploy-testnet.sh

demo-testnet:
	scripts/demo-testnet.sh

lock-vault-testnet:
	scripts/lock-vault.sh
