"""Sample near CLI output, trimmed from a real testnet run."""

SPEND_TRUE = """├  Transaction Execution Info:
│    Gas burned: 0.313 Tgas
│    Transaction ID: 8biwosgMUfdvkqaG2HJajQyWKBL97YYfXSeMagBeE9BA
│
├  Function execution logs:
│    Logs [leashmusa-vault.testnet]:
│      EVENT_JSON:{"standard":"leash","version":"0.1.2","event":"spend_allowed","data":[{"agent":"leashmusa-agent.testnet","to":"leashmusa-payee.testnet","amount":"500000000000000000000000","memo":"invoice 1"}]}
│    Logs [leashmusa-payee.testnet]:   No logs

Function execution return value (printed to stdout):
true


The "spend" call to <leashmusa-vault.testnet> on behalf of <leashmusa-agent.testnet> succeeded.

Here is your console command if you need to script it or re-run:
    near contract call-function as-transaction leashmusa-vault.testnet spend json-args '{}'
"""

SPEND_FALSE = """├  Transaction Execution Info:
│    Transaction ID: 8xbaUvmVhx9QVXSXzM9RwHRc2S8gPfNC8GDgTFG3StHf
├  Function execution logs:
│    Logs [leashmusa-vault.testnet]:
│      EVENT_JSON:{"standard":"leash","version":"0.1.2","event":"spend_denied","data":[{"agent":"leashmusa-agent.testnet","to":"leashmusa-attacker.testnet","amount":"500000000000000000000000","reason":"not_allowlisted","memo":"x"}]}

Function execution return value (printed to stdout):
false


The "spend" call to <leashmusa-vault.testnet> on behalf of <leashmusa-agent.testnet> succeeded.
"""

VIEW_STATS = """├  Logs:
│    No logs
Function execution return value (printed to stdout):
[
  "2",
  "3"
]


Here is your console command if you need to script it or re-run:
    near contract call-function as-read-only leashmusa-vault.testnet get_stats json-args {} network-config testnet now
"""

# What stderr looks like when the CLI is piped: the value is not repeated here.
SPLIT_STDERR_SPEND = """├  Transaction Execution Info:
│    Transaction ID: 8xbaUvmVhx9QVXSXzM9RwHRc2S8gPfNC8GDgTFG3StHf
├  Function execution logs:
│    Logs [leashmusa-vault.testnet]:
│      EVENT_JSON:{"standard":"leash","version":"0.1.2","event":"spend_denied","data":[{"agent":"leashmusa-agent.testnet","to":"leashmusa-attacker.testnet","amount":"500000000000000000000000","reason":"not_allowlisted","memo":"x"}]}

Function execution return value (printed to stdout):
"""

SPLIT_STDERR_VIEW = """├  Logs:
│    No logs
Function execution return value (printed to stdout):
"""
