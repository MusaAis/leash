"""Hide the private route values (recipient, refund account) in anything that is printed."""

from __future__ import annotations

import os
import re

HIDDEN = (("LEASH_ROUTE_RECIPIENT", "<RECIPIENT>"), ("LEASH_DRY_REFUND_TO", "<REFUND_TO>"))


def scrub(text: str, extra: tuple[tuple[str, str], ...] = ()) -> str:
    """Replace each private value with its label, ignoring case. `extra` holds (value, label) pairs."""
    pairs = [(os.environ.get(name, ""), label) for name, label in HIDDEN] + list(extra)
    for value, label in sorted(pairs, key=lambda pair: -len(pair[0])):
        if value:
            text = re.sub(re.escape(value), label, text, flags=re.IGNORECASE)
    return text
