"""Lists the host imports of a wasm file. Usage: python3 scripts/wasm_imports.py FILE. First line: "<n> imports"."""
from __future__ import annotations

import sys


def leb(data: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


def _limits(data: bytes, i: int) -> int:
    flag = data[i]
    i += 1
    _, i = leb(data, i)
    if flag & 1:
        _, i = leb(data, i)
    return i


def imports(data: bytes) -> list[tuple[str, str]]:
    """(module, name) of every import. Raises ValueError on bytes that are not a wasm module."""
    if data[:4] != b"\0asm":
        raise ValueError("not a wasm file")
    i = 8
    while i < len(data):
        section = data[i]
        size, i = leb(data, i + 1)
        if section != 2:
            i += size
            continue
        count, j = leb(data, i)
        found = []
        for _ in range(count):
            length, j = leb(data, j)
            module = data[j:j + length].decode()
            j += length
            length, j = leb(data, j)
            name = data[j:j + length].decode()
            j += length
            kind = data[j]
            j += 1
            if kind == 0:
                _, j = leb(data, j)
            elif kind == 1:
                j = _limits(data, j + 1)
            elif kind == 2:
                j = _limits(data, j)
            elif kind == 3:
                j += 2
            elif kind == 4:
                j += 1
                _, j = leb(data, j)
            else:
                raise ValueError(f"unknown import kind {kind}")
            found.append((module, name))
        return found
    return []


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: wasm_imports.py FILE", file=sys.stderr)
        return 2
    with open(argv[1], "rb") as handle:
        found = imports(handle.read())
    print(f"{len(found)} imports")
    for module, name in found:
        print(module, name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
