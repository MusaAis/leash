SAMPLES ?= 1
AMOUNT ?= 0.5
.PHONY: test build fmt lint check check-docs test-agent agent-models agent-dry accounts-testnet deploy-testnet demo-testnet lock-vault-testnet policy-testnet agent-check agent-run attack-testnet attack-llm-testnet intents-tokens intents-dry intents-wet-probe wrap-storage agent-signature

test:
	cd contract && cargo test

test-agent:
	cd agent && python3 -m unittest discover -s tests -t .

build:
	cd contract && cargo near build non-reproducible-wasm

fmt:
	cd contract && cargo fmt

lint:
	cd contract && cargo fmt --check && cargo clippy --all-targets -- -D warnings

check: lint test test-agent check-docs

check-docs:
	python3 scripts/check_docs.py

accounts-testnet:
	scripts/accounts-testnet.sh

deploy-testnet: build
	scripts/deploy-testnet.sh

demo-testnet:
	scripts/demo-testnet.sh

lock-vault-testnet:
	scripts/lock-vault.sh

policy-testnet:
	scripts/policy-testnet.sh

agent-check:
	cd agent && python3 -m leash_agent check

agent-models:
	cd agent && python3 -m leash_agent models

agent-dry:
	cd agent && python3 -m leash_agent run "$(TASK)" --dry-run

agent-run:
	cd agent && python3 -m leash_agent run "$(TASK)"

attack-testnet:
	cd agent && python3 -m leash_agent attack --mode compromised

attack-llm-testnet:
	cd agent && python3 -m leash_agent attack --mode llm --samples $(SAMPLES)

intents-tokens:
	cd agent && python3 -m leash_agent intents-tokens $(if $(SYMBOL),--symbol $(SYMBOL)) $(if $(CHAIN),--chain $(CHAIN))

intents-dry:
	cd agent && python3 -m leash_agent intents-dry --amount $(AMOUNT)

agent-signature:
	cd agent && python3 -m leash_agent agent-signature $(foreach m,$(MODELS),--model $(m))

wrap-storage:
	cd agent && python3 -m leash_agent wrap-storage $(foreach a,$(ACCOUNT),--account $(a))

intents-wet-probe:
	cd agent && python3 -m leash_agent intents-wet-probe $(if $(PROBE_AMOUNT),--amount $(PROBE_AMOUNT)) --yes
