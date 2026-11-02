"""Configuration from environment variables and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class EnvFileError(ValueError):
    """The .env file is not usable. The message never contains a value."""


def _strip_export(line: str) -> str:
    return line[7:].lstrip() if line.startswith("export ") else line


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into the environment without overriding existing values."""
    if not path.is_file():
        return
    seen: dict[str, list[int]] = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = _strip_export(raw.strip())
        if line and not line.startswith("#") and "=" in line:
            key = line.partition("=")[0].strip()
            if key.startswith("LEASH_ALLOW_"):
                raise EnvFileError(f"{path.name} line {number}: {key} is not allowed in the file. Set it in the process environment for one command.")
            seen.setdefault(key, []).append(number)
    repeated = {key: rows for key, rows in seen.items() if len(rows) > 1}
    if repeated:
        details = "; ".join(f"{key} on lines {', '.join(map(str, rows))}" for key, rows in sorted(repeated.items()))
        raise EnvFileError(f"{path.name} repeats a key: {details}. Keep one line per key.")
    for raw in path.read_text().splitlines():
        line = _strip_export(raw.strip())
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] not in ('"', "'"):
            value = value.split(" #", 1)[0].strip()  # an unquoted value ends at an inline comment
        os.environ.setdefault(key.strip(), value.strip('"').strip("'"))


ACCOUNT_SUFFIX = {"mainnet": "near", "testnet": "testnet"}
WNEAR = {"mainnet": "wrap.near", "testnet": "wrap.testnet"}
# `spend_intent` runs a four-action batch, so it has its own gas setting. The contract panics below the minimum.
INTENT_GAS_MIN_TGAS = 120
INTENT_GAS_MAX_TGAS = 300
INTENT_GAS_DEFAULT_TGAS = 150


def intent_gas_tgas(raw: str | None) -> int:
    """Read LEASH_INTENT_GAS_TGAS: whole Tgas between the minimum and the maximum, default 150."""
    if raw is None or raw.strip() == "":
        return INTENT_GAS_DEFAULT_TGAS
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        raise EnvFileError(f"LEASH_INTENT_GAS_TGAS must be a whole number from {INTENT_GAS_MIN_TGAS} to {INTENT_GAS_MAX_TGAS}")
    value = int(text)
    if not INTENT_GAS_MIN_TGAS <= value <= INTENT_GAS_MAX_TGAS:
        raise EnvFileError(f"LEASH_INTENT_GAS_TGAS must be from {INTENT_GAS_MIN_TGAS} to {INTENT_GAS_MAX_TGAS}")
    return value


@dataclass(frozen=True)
class Config:
    network: str
    vault: str
    owner: str
    agent: str
    payee: str
    attacker: str
    lookalike: str
    ai_base_url: str
    ai_model: str
    ai_api_key: str | None
    gas: str = "30 Tgas"  # for `spend` only; `spend_intent` uses `intent_gas`
    guardian: str = ""
    wnear: str = ""
    intent_gas_tgas: int = INTENT_GAS_DEFAULT_TGAS

    @property
    def intent_gas(self) -> str:
        return f"{self.intent_gas_tgas} Tgas"

    @classmethod
    def from_env(cls) -> "Config":
        root = Path(__file__).resolve().parents[2]
        load_env_file(root / ".env")
        network = os.environ.get("NETWORK", "testnet")
        prefix = os.environ.get("LEASH_PREFIX", "leashdemo")
        suffix = ACCOUNT_SUFFIX.get(network, network)  # mainnet accounts end in .near, not .mainnet
        return cls(
            network=network,
            vault=f"{prefix}-vault.{suffix}",
            owner=f"{prefix}-owner.{suffix}",
            agent=f"{prefix}-agent.{suffix}",
            payee=f"{prefix}-payee.{suffix}",
            attacker=f"{prefix}-attacker.{suffix}",
            lookalike=f"{prefix}-paye3.{suffix}",
            ai_base_url=os.environ.get("NEAR_AI_BASE_URL", "https://cloud-api.near.ai/v1"),
            ai_model=os.environ.get("NEAR_AI_MODEL", "zai-org/GLM-5.1-FP8"),
            ai_api_key=os.environ.get("NEAR_AI_API_KEY") or None,
            guardian=f"{prefix}-guardian.{suffix}",
            wnear=os.environ.get("LEASH_WNEAR") or WNEAR.get(network, ""),
            intent_gas_tgas=intent_gas_tgas(os.environ.get("LEASH_INTENT_GAS_TGAS")),
        )
