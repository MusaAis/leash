#!/usr/bin/env python3
"""Replays logged decision events against the head the vault reports.

Usage: replay_head.py EVENTS_FILE HEAD_HEX

Input: the decision events of the receipts that succeeded, in block order,
one event per line, LF line endings, UTF-8 without a byte order mark. A line
may carry the EVENT_JSON: prefix. A failed receipt can leave a log line
behind while its head change is reverted, so such a line must not be in the
file; replaying it prints "chain BROKEN". Lines are hashed exactly as written.

Output: "chain OK" or "chain BROKEN".
Exit code: 0 OK, 1 BROKEN, 2 usage, 3 the file cannot be read.
"""
import hashlib
import re
import sys

PREFIX = "EVENT_JSON:"
GENESIS = bytes(32)
HEAD_RE = re.compile(r"[0-9a-f]{64}")


def payload(line: str) -> str:
    return line[len(PREFIX):] if line.startswith(PREFIX) else line


def head_of(lines: list[str]) -> str:
    head = GENESIS
    for line in lines:
        head = hashlib.sha256(head + payload(line).encode("utf-8")).digest()
    return head.hex()


def split_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def verify(lines: list[str], head_hex: str) -> bool:
    return bool(HEAD_RE.fullmatch(head_hex)) and head_of(lines) == head_hex


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: replay_head.py EVENTS_FILE HEAD_HEX", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8", newline="") as handle:
            lines = split_lines(handle.read())
    except (OSError, UnicodeDecodeError) as error:
        print(f"chain ERROR: cannot read the events file ({type(error).__name__})", file=sys.stderr)
        return 3
    ok = verify(lines, argv[2])
    print("chain OK" if ok else "chain BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
