#!/usr/bin/env python3
"""Check that the docs still say what the code does. `make check` runs it, so `release.sh` stops on stale docs.

It compares: STATE.md and CHANGELOG.md with the git tags, the test counts, the denial reasons, the attack
count, the cost-limit defaults, the make targets and the relative links. It does not check live results:
those come from a recorded run (see docs/testnet-proof.md).
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
# environment variable -> constant in agent/leash_agent/brain.py
LIMITS = {
    "NEAR_AI_MAX_TOKENS": "DEFAULT_MAX_TOKENS",
    "LEASH_MAX_MODEL_CALLS": "DEFAULT_MAX_CALLS",
    "LEASH_MAX_TOTAL_TOKENS": "DEFAULT_MAX_TOTAL_TOKENS",
    "LEASH_MAX_INPUT_CHARS": "DEFAULT_MAX_INPUT_CHARS",
    "LEASH_MAX_DAILY_CALLS": "DEFAULT_MAX_DAILY_CALLS",
    "LEASH_MAX_DAILY_TOKENS": "DEFAULT_MAX_DAILY_TOKENS",
}

failures: list[str] = []


def text(path: str) -> str:
    return (ROOT / path).read_text()


def report(name: str, problems: list[str]) -> None:
    print(f"{'FAIL' if problems else 'ok  '}  {name}")
    for problem in problems:
        print(f"        {problem}")
        failures.append(f"{name}: {problem}")


def numbers(doc: str, pattern: str) -> list[int]:
    return [int(m) for m in re.findall(pattern, text(doc))]


def check_versions() -> list[str]:
    problems = []
    line = re.search(r"Version:\s*(v\d+\.\d+\.\d+)([^\n]*)", text("docs/STATE.md"))
    if not line:
        return ["docs/STATE.md has no `Version: vX.Y.Z` line"]
    version, rest = line.group(1), line.group(2)
    tags = subprocess.run(["git", "tag", "-l"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if version in tags and re.search(r"candidate|unreleased|in progress", rest, re.I):
        problems.append(f"STATE.md calls {version} a candidate, but the tag {version} exists")
    top = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text("CHANGELOG.md"), re.M)
    if not top or f"v{top.group(1)}" != version:
        problems.append(f"STATE.md says {version} but the newest released CHANGELOG entry is {top and top.group(1)}")
    return problems


def check_test_counts() -> list[str]:
    suite = unittest.TestLoader().discover(str(ROOT / "agent" / "tests"), top_level_dir=str(ROOT / "agent"))
    agent = suite.countTestCases()
    contract = text("contract/src/lib.rs").count("#[test]")
    claims = [
        ("README.md", r"Agent unit tests \| (\d+) passed", agent), ("README.md", r"Ran (\d+) tests", agent),
        ("docs/STATE.md", r"(\d+) agent tests", agent),
        ("README.md", r"Contract unit tests \| (\d+) passed", contract),
        ("README.md", r"test result: ok\. (\d+) passed", contract), ("docs/STATE.md", r"(\d+) contract tests", contract),
    ]
    problems = []
    for doc, pattern, actual in claims:
        found = numbers(doc, pattern)
        if not found:
            problems.append(f"{doc}: no claim matching /{pattern}/ (reworded? update scripts/check_docs.py)")
        problems += [f"{doc} says {n}, the code has {actual} (/{pattern}/)" for n in found if n != actual]
    return problems


def check_denial_reasons() -> list[str]:
    source = text("contract/src/lib.rs")
    body = source[source.index("pub fn spend"):source.index("match reason {")]
    in_code = set(re.findall(r'Some\("([a-z_]+)"\)', body))
    line = re.search(r"Denial reasons: ([^\n]+)", text("README.md"))
    in_doc = set(re.findall(r"`([a-z_]+)`", line.group(1))) if line else set()
    if in_doc != in_code:
        return [f"README lists {sorted(in_doc)}, the contract has {sorted(in_code)}"]
    return []


def check_attack_count() -> list[str]:
    from leash_agent.attacks import build_attacks
    from leash_agent.config import Config

    cfg = Config("testnet", "v", "o", "a", "p", "x", "p3", "https://h", "m", None)
    actual, problems = len(build_attacks(cfg)), []
    for doc in ("README.md", "docs/agent.md"):
        for word in re.findall(r"\b(\w+) (?:attack types|kinds of manipulation|attack texts)\b", text(doc), re.I):
            n = NUMBER_WORDS.get(word.lower(), int(word) if word.isdigit() else None)
            if n is not None and n != actual:
                problems.append(f"{doc} says {word} attacks, the suite has {actual}")
    return problems


def check_limits() -> list[str]:
    code = dict(re.findall(r"^(DEFAULT_\w+)\s*=\s*(\d+)", text("agent/leash_agent/brain.py"), re.M))
    problems = []
    for env, const in LIMITS.items():
        row = re.search(rf"\|\s*`{env}`\s*\|\s*(\d+)\s*\|", text("docs/agent.md"))
        if not row:
            problems.append(f"docs/agent.md has no row for {env}")
        elif row.group(1) != code.get(const):
            problems.append(f"docs/agent.md says {env} = {row.group(1)}, the code default is {code.get(const)}")
        example = re.search(rf"{env}=(\d+)", text(".env.example"))
        if example and example.group(1) != code.get(const):
            problems.append(f".env.example says {env}={example.group(1)}, the code default is {code.get(const)}")
    return problems


def check_make_targets() -> list[str]:
    targets = set(re.findall(r"^([a-z][a-z-]*):", text("Makefile"), re.M))
    problems = []
    for doc in ["README.md", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))]:
        for target in set(re.findall(r"\bmake ([a-z][a-z-]*)", text(doc))):
            if target not in targets:
                problems.append(f"{doc} mentions `make {target}`, which does not exist")
    return problems


def check_links() -> list[str]:
    problems = []
    for doc in ["README.md", "SECURITY.md", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))]:
        for link in re.findall(r"\]\(([^)\s]+)\)", text(doc)):
            if re.match(r"(https?:|mailto:|#)", link):
                continue
            if not ((ROOT / doc).parent / link.split("#")[0]).exists():
                problems.append(f"{doc} links to {link}, which does not exist")
    return problems


CHECKS = [
    ("STATE.md and CHANGELOG.md match the git tags", check_versions),
    ("test counts in the docs match the code", check_test_counts),
    ("denial reasons in the README match the contract", check_denial_reasons),
    ("attack count in the docs matches the suite", check_attack_count),
    ("cost-limit defaults in the docs match the code", check_limits),
    ("every `make` target in the docs exists", check_make_targets),
    ("relative links in the docs resolve", check_links),
]

if __name__ == "__main__":
    for name, check in CHECKS:
        report(name, check())
    print(f"\n{'Docs are stale: fix them before any push.' if failures else 'Docs match the code.'}")
    raise SystemExit(1 if failures else 0)
