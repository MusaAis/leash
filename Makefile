SAMPLES ?= 1
AMOUNT ?= 0.5
# Values typed on a command line reach the recipes through the environment and are quoted there.
export TASK AMOUNT SYMBOL CHAIN PROBE_AMOUNT SAMPLES MODELS ACCOUNT
.PHONY: build-info test build fmt lint check check-docs test-agent agent-models agent-dry accounts-testnet deploy-testnet demo-testnet lock-vault-testnet policy-testnet agent-check agent-run attack-testnet attack-llm-testnet intents-tokens intents-dry intents-wet-probe wrap-storage agent-signature guard intents-status intents-config intents-routes intents-approve intents-spend intents-quote intents-keys panic-key

test:
	cd contract && cargo test --locked

test-agent:
	cd agent && python3 -m unittest discover -s tests -t .

build-info:
	scripts/build-info.sh

build:
	cd contract && cargo near build non-reproducible-wasm

fmt:
	cd contract && cargo fmt

lint:
	cd contract && cargo fmt --check && cargo clippy --locked --all-targets -- -D warnings

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
	cd agent && python3 -m leash_agent run "$$TASK" --dry-run

agent-run:
	cd agent && python3 -m leash_agent run "$$TASK"

attack-testnet:
	cd agent && python3 -m leash_agent attack --mode compromised

attack-llm-testnet:
	cd agent && python3 -m leash_agent attack --mode llm --samples "$$SAMPLES"

intents-tokens:
	cd agent && python3 -m leash_agent intents-tokens $(if $(SYMBOL),--symbol "$$SYMBOL") $(if $(CHAIN),--chain "$$CHAIN")

intents-dry:
	cd agent && python3 -m leash_agent intents-dry --amount "$$AMOUNT"

agent-signature:
	cd agent && set -f && set -- && for m in $$MODELS; do set -- "$$@" --model "$$m"; done && python3 -m leash_agent agent-signature "$$@"

wrap-storage:
	cd agent && set -f && set -- && for a in $$ACCOUNT; do set -- "$$@" --account "$$a"; done && python3 -m leash_agent wrap-storage "$$@"

intents-wet-probe:
	cd agent && python3 -m leash_agent intents-wet-probe $(if $(PROBE_AMOUNT),--amount "$$PROBE_AMOUNT") --yes

# The call path. It sends only with YES=1 (any other value, including YES=0 or "1 0", only prints the plan).
# Owner commands also need CONFIRM_VAULT=<vault name>. Outside testnet the deposit account and the hash come from the saved quote.
# Values typed on a command line reach the recipes through the environment and are quoted, so they are never read as shell code.
export CONFIRM_VAULT DEPOSIT SWAP_AMOUNT MINUTES QUOTE_HASH ROUTE
ROUTE ?= 0
ifeq ($(strip $(YES)),1)
SEND = --yes
else
SEND =
endif
ifeq ($(strip $(ASIDE)),1)
ASIDE_FLAG = --aside
else
ASIDE_FLAG =
endif
intents-status:
	cd agent && python3 -m leash_agent intents-status

intents-keys:
	cd agent && python3 -m leash_agent intents-keys $(SEND) $(ASIDE_FLAG)

panic-key:
	cd agent && python3 -m leash_agent panic-key $(SEND)

intents-config:
	cd agent && python3 -m leash_agent intents-config $(if $(CONFIRM_VAULT),--confirm-vault "$$CONFIRM_VAULT") $(SEND)

intents-routes:
	cd agent && python3 -m leash_agent intents-routes $(if $(CONFIRM_VAULT),--confirm-vault "$$CONFIRM_VAULT") $(SEND)

intents-quote:
	cd agent && python3 -m leash_agent intents-quote --route "$$ROUTE" --amount "$$SWAP_AMOUNT" $(SEND)

intents-approve:
	cd agent && python3 -m leash_agent intents-approve --route "$$ROUTE" --amount "$$SWAP_AMOUNT" $(if $(DEPOSIT),--deposit-account "$$DEPOSIT") $(if $(MINUTES),--minutes "$$MINUTES") $(if $(QUOTE_HASH),--quote-hash "$$QUOTE_HASH") $(SEND)

intents-spend:
	cd agent && python3 -m leash_agent intents-spend --route "$$ROUTE" --amount "$$SWAP_AMOUNT" $(if $(DEPOSIT),--deposit-account "$$DEPOSIT") $(SEND)

NETWORK ?= testnet
export NETWORK ACCOUNTS
guard:
	cd agent && python3 -m leash_agent guard --network "$$NETWORK" $$ACCOUNTS
