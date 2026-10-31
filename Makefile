.PHONY: test build fmt lint check

test:
	cd contract && cargo test

build:
	cd contract && cargo near build non-reproducible-wasm

fmt:
	cd contract && cargo fmt

lint:
	cd contract && cargo clippy --all-targets -- -D warnings

check: lint test
