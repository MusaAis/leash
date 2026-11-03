SAMPLES ?= 1
AMOUNT ?= 0.5
.PHONY: test build fmt lint check check-docs test-agent agent-models agent-dry accounts-testnet deploy-testnet demo-testnet lock-vault-testnet policy-testnet agent-check agent-run attack-testnet attack-llm-testnet intents-tokens intents-dry intents-wet-probe wrap-storage agent-signature guard intents-status intents-config intents-routes intents-approve intents-spend intents-quote intents-keys

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

# The call path. It sends only with YES=1 (any other value, including YES=0, only prints the plan).
# Owner commands also need CONFIRM_VAULT=<vault name>. Outside testnet the deposit account and the hash come from the saved quote.
ROUTE ?= 0
SEND = $(if $(filter 1,$(YES)),--yes)
intents-status:
	cd agent && python3 -m leash_agent intents-status

intents-keys:
	cd agent && python3 -m leash_agent intents-keys $(SEND) $(if $(filter 1,$(ASIDE)),--aside)

intents-config:
	cd agent && python3 -m leash_agent intents-config $(if $(CONFIRM_VAULT),--confirm-vault $(CONFIRM_VAULT)) $(SEND)

intents-routes:
	cd agent && python3 -m leash_agent intents-routes $(if $(CONFIRM_VAULT),--confirm-vault $(CONFIRM_VAULT)) $(SEND)

intents-quote:
	cd agent && python3 -m leash_agent intents-quote --route $(ROUTE) --amount $(SWAP_AMOUNT) $(SEND)

intents-approve:
	cd agent && python3 -m leash_agent intents-approve --route $(ROUTE) --amount $(SWAP_AMOUNT) $(if $(DEPOSIT),--deposit-account $(DEPOSIT)) $(if $(MINUTES),--minutes $(MINUTES)) $(if $(QUOTE_HASH),--quote-hash $(QUOTE_HASH)) $(SEND)

intents-spend:
	cd agent && python3 -m leash_agent intents-spend --route $(ROUTE) --amount $(SWAP_AMOUNT) $(if $(DEPOSIT),--deposit-account $(DEPOSIT)) $(SEND)

NETWORK ?= testnet
guard:
	cd agent && python3 -m leash_agent guard --network $(NETWORK) $(ACCOUNTS)
