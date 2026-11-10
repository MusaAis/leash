"""The attempt log: one JSON object per line, mode 0600, every text through scrub()."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterator

from leash_agent.redact import scrub

PUBLIC_FIELDS = (
    "id", "ts", "tier", "message", "outcome", "fooled", "funds_lost", "to", "amount_near",
    "tx_id", "denial_reason", "reason",
)


class LogError(Exception):
    """The log file cannot be used safely."""


def _scrubbed(value: object) -> object:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(str(k)): _scrubbed(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrubbed(v) for v in value]
    return value


def public_record(record: dict) -> dict:
    """The fields a visitor may see. The visitor hash, tokens, cost and error detail stay out."""
    return {key: record[key] for key in PUBLIC_FIELDS if key in record}


class AttemptLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
            mode = self.path.stat().st_mode & 0o777
        except OSError as exc:
            raise LogError(f"the log file cannot be opened: {exc.strerror}") from exc
        if mode & 0o077:
            raise LogError(f"the log file mode is {oct(mode)}; it must be 0600")

    def append(self, record: dict) -> None:
        """Write one line. Raises OSError when the write fails."""
        line = json.dumps(_scrubbed(record), ensure_ascii=True, separators=(",", ":"))
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            with os.fdopen(fd, "a") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def replay(self) -> Iterator[dict]:
        """Every readable record, oldest first. Lines that are not a JSON object are skipped."""
        with open(self.path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    yield data
