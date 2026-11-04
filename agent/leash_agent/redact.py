"""Hide the private route values (recipient, refund account) in anything that is printed."""

from __future__ import annotations

import os
import re
from urllib.parse import quote

HIDDEN = (("LEASH_ROUTE_RECIPIENT", "<RECIPIENT>"), ("LEASH_DRY_REFUND_TO", "<REFUND_TO>"))


def scrub(text: str, extra: tuple[tuple[str, str], ...] = ()) -> str:
    """Replace each private value with its label, ignoring case. `extra` holds (value, label) pairs."""
    pairs = []
    for value, label in [(os.environ.get(name, ""), label) for name, label in HIDDEN] + list(extra):
        if not value:
            continue
        pairs.append((value, label))
        if value[:2].lower() == "0x" and len(value) > 10:
            pairs.append((value[2:], label))  # the same address without its prefix
        encoded = quote(value, safe="")
        if encoded != value:
            pairs.append((encoded, label))  # percent-encoded echo (any letter case)
    for value, label in sorted(pairs, key=lambda pair: -len(pair[0])):
        if value:
            text = re.sub(re.escape(value), label, text, flags=re.IGNORECASE)
    return text
