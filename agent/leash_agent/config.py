"""Configuration from environment variables and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into the environment without overriding existing values."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
    gas: str = "30 Tgas"

    @classmethod
    def from_env(cls) -> "Config":
        root = Path(__file__).resolve().parents[2]
        load_env_file(root / ".env")
        network = os.environ.get("NETWORK", "testnet")
        prefix = os.environ.get("LEASH_PREFIX", "leashdemo")
        return cls(
            network=network,
            vault=f"{prefix}-vault.{network}",
            owner=f"{prefix}-owner.{network}",
            agent=f"{prefix}-agent.{network}",
            payee=f"{prefix}-payee.{network}",
            attacker=f"{prefix}-attacker.{network}",
            lookalike=f"{prefix}-paye3.{network}",
            ai_base_url=os.environ.get("NEAR_AI_BASE_URL", "https://cloud-api.near.ai/v1"),
            ai_model=os.environ.get("NEAR_AI_MODEL", "zai-org/GLM-5.1-FP8"),
            ai_api_key=os.environ.get("NEAR_AI_API_KEY") or None,
        )
