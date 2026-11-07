#!/usr/bin/env python3
"""Check that the docs still say what the code does. `make check` runs it, so `release.sh` stops on stale docs.

It compares: STATE.md and CHANGELOG.md with the git tags, the test counts, the denial reasons, the attack
count, the cost-limit defaults, the make targets and the relative links. It does not check live results:
those come from a recorded run (see docs/testnet-proof.md).
"""

from __future__ import annotations

import posixpath
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


VERSION_LINE = r"Version:\s*(v\d+\.\d+\.\d+(?:-fix\d+)?)([^\n]*)"
CHANGELOG_TOP = r"^## \[(\d+\.\d+\.\d+(?:-fix\d+)?)\]"


def check_versions() -> list[str]:
    problems = []
    line = re.search(VERSION_LINE, text("docs/STATE.md"))
    if not line:
        return ["docs/STATE.md has no `Version: vX.Y.Z` line"]
    version, rest = line.group(1), line.group(2)
    tags = subprocess.run(["git", "tag", "-l"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if version in tags and re.search(r"candidate|unreleased|in progress", rest, re.I):
        problems.append(f"STATE.md calls {version} a candidate, but the tag {version} exists")
    top = re.search(CHANGELOG_TOP, text("CHANGELOG.md"), re.M)
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


def denial_reasons(source: str) -> set[str]:
    start = source.find("fn check_spend(")
    if start < 0:
        return set()
    end = source.find("\n}\n", start)
    body = source[start:end if end >= 0 else len(source)]
    return set(re.findall(r'Err\("([a-z_]+)"\)', body))


def denial_problems(source: str, readme: str) -> list[str]:
    in_code = denial_reasons(source)
    if not in_code:
        return ["no denial reasons found in the contract (expected `Err(\"reason\")` returns in fn check_spend)"]
    line = re.search(r"Denial reasons: ([^\n]+)", readme)
    in_doc = set(re.findall(r"`([a-z_]+)`", line.group(1))) if line else set()
    if in_doc != in_code:
        return [f"README lists {sorted(in_doc)}, the contract has {sorted(in_code)}"]
    return []


def check_denial_reasons() -> list[str]:
    return denial_problems(text("contract/src/lib.rs"), text("README.md"))


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



STALE = [r"not released", r"in progress", r"not deployed", r"placeholders?", r"to be measured", r"no LEASH contract",
         r"reported by", r"\(reported", r"reported, not shown", r"owner reports", r"owner's report", r"owner's statement",
         r"inferred", r"unverified", r"not yet verified", r"rehearsal records", r"creation[ -]key"]
ROLES = "|".join(["LE" + "AD", "Re" + "viewer", "Bui" + "lder", "Advi" + "ser"])
PRIVATE = [r"\b(?:" + ROLES + r")\b", r"\bReview \d+\b", r"\b[A-Z]-\d{1,3}\b", r"\bP\d+-\d+\b", r"\bR\d+-\d+\b",
           r"\bPatch \d+\b", r"\b" + "Mu" + r"sa\b"]


def stale_phrases(name: str, body: str) -> list[str]:
    return [f"{name} contains `{p}`" for p in STALE if re.search(p, body, re.I)]


def private_shapes(name: str, body: str) -> list[str]:
    return [f"{name} matches the private shape /{p}/" for p in PRIVATE if re.search(p, body)]


def contract_part(source: str) -> str:
    return source[: source.index("#[cfg(test)]")] if "#[cfg(test)]" in source else source


# Methods in the contract that the docs must not name until the tag below exists.
UNRELEASED_UNTIL = "v0.3.2"
UNRELEASED_METHODS: list[str] = ["would_spend", "would_spend_intent", "get_head_hash"]


def contract_methods(source: str) -> list[str]:
    part = contract_part(source)
    return re.findall(r"^\s*pub fn (\w+)\(", part[part.index("pub fn new("):], re.M)


def interface_gaps(source: str, readme: str, skip: tuple[str, ...] | list[str] = ()) -> list[str]:
    names = [n for n in contract_methods(source) if n not in skip]
    return [f"README has no entry for the contract method `{n}`" for n in names if not re.search(r"`%s[`(]" % n, readme)]


def unreleased_problems(source: str, docs: dict[str, str], listed: list[str], until: str, tags: set[str]) -> list[str]:
    methods = set(contract_methods(source))
    problems = []
    for name in listed:
        if name not in methods:
            problems.append(f"`{name}` is on the unreleased list but the contract has no such method")
        problems += [f"{path} names `{name}` before {until} is tagged"
                     for path, body in docs.items() if re.search(r"\b%s\b" % re.escape(name), body)]
    if listed and tags and until in tags:
        problems.append(f"{until} is tagged: empty the unreleased list and add the README entries")
    return problems


def event_gaps(source: str, readme: str) -> list[str]:
    events = sorted(set(re.findall(r'emit\(\s*"([a-z_]+)"', contract_part(source))))
    line = next((ln for ln in readme.splitlines() if ln.startswith("Events:")), "")
    return [f"README event list has no `{e}`" for e in events if f"`{e}`" not in line]


def badge_problems(readme: str, has_mainnet_proof: bool) -> list[str]:
    badge = re.search(r"badge/network-([^\"\s]*?)-informational", readme)
    if not badge:
        return ["README has no network badge"]
    if has_mainnet_proof and "mainnet" not in badge.group(1).lower():
        return ["README badge omits mainnet, but docs/mainnet-proof.md exists"]
    return []


def pin_problems(cargo_toml: str, build_md: str) -> list[str]:
    pins = set(re.findall(r'near-sdk[^\n]*"=(\d+\.\d+\.\d+)"', cargo_toml))
    if len(pins) != 1:
        return [f"contract/Cargo.toml must pin near-sdk exactly once with =X.Y.Z (found {sorted(pins)})"]
    return [] if pins.copy().pop() in build_md else ["docs/build.md does not state the near-sdk version of the pin"]


def wasm_problems(build_md: str, proof_md: str) -> list[str]:
    problems = []
    for label, pattern in (("sha256", r"sha256\s*\|\s*`([0-9a-f]{64})`"), ("code hash", r"base58\)\s*\|\s*`(\w+)`")):
        found = re.search(pattern, build_md)
        if not found:
            problems.append(f"docs/build.md has no {label} row")
        elif found.group(1) not in proof_md:
            problems.append(f"docs/mainnet-proof.md does not contain the {label} of docs/build.md")
    return problems


def snapshot_problems(name: str, body: str) -> list[str]:
    """Dated values live in the proof page: an ISO date may appear in links and code only."""
    prose = re.sub(r"```.*?```", "", body, flags=re.S)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    prose = re.sub(r"\[[^\]]*\]\([^)]*\)", "", prose)
    return [f"{name} has a date in prose: {d}" for d in re.findall(r"\b20\d\d-\d\d-\d\d\b", prose)]


def slug_set(body: str) -> set[str]:
    seen: dict[str, int] = {}
    slugs = set()
    for heading in re.findall(r"^#+\s+(.*)$", re.sub(r"```.*?```", "", body, flags=re.S), re.M):
        base = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading.strip().lower()))
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs


def anchor_gaps(name: str, body: str, targets: dict[str, str]) -> list[str]:
    problems = []
    for path, frag in re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", body):
        if re.match(r"(https?:|mailto:)", path):
            continue
        key = posixpath.normpath(posixpath.join(posixpath.dirname(name), path)) if path else name
        other = targets.get(key)
        if other is not None and frag not in slug_set(other):
            problems.append(f"{name} links to {path}#{frag}, which has no such heading")
    return problems


def doc_paths() -> list[str]:
    docs = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))
    adrs = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs/adr").glob("*.md"))
    return ["README.md", "SECURITY.md", *docs, *adrs]


def check_stale() -> list[str]:
    return [p for d in dict.fromkeys([*doc_paths(), "CHANGELOG.md"]) for p in stale_phrases(d, text(d))]


def check_private() -> list[str]:
    return [p for d in [*doc_paths(), "CHANGELOG.md"] for p in private_shapes(d, text(d))]


def check_interface() -> list[str]:
    return interface_gaps(text("contract/src/lib.rs"), text("README.md"), UNRELEASED_METHODS)


def check_unreleased() -> list[str]:
    tags = set(subprocess.run(["git", "tag", "-l"], cwd=ROOT, capture_output=True, text=True).stdout.split())
    docs = {d: text(d) for d in doc_paths()}
    return unreleased_problems(text("contract/src/lib.rs"), docs, UNRELEASED_METHODS, UNRELEASED_UNTIL, tags)


def check_events() -> list[str]:
    return event_gaps(text("contract/src/lib.rs"), text("README.md"))


def check_badge() -> list[str]:
    return badge_problems(text("README.md"), (ROOT / "docs/mainnet-proof.md").exists())


def readme_pin_problems(readme: str, cargo_toml: str, toolchain_toml: str) -> list[str]:
    row = re.search(r"Rust (\d+\.\d+\.\d+) and near-sdk =(\d+\.\d+\.\d+)", readme)
    if not row:
        return ["README has no row `Rust X.Y.Z and near-sdk =X.Y.Z` for the pinned build inputs"]
    channel = re.search(r'channel\s*=\s*"([^"]+)"', toolchain_toml)
    pins = set(re.findall(r'near-sdk[^\n]*"=(\d+\.\d+\.\d+)"', cargo_toml))
    problems = []
    if not channel or channel.group(1) != row.group(1):
        problems.append(f"README says Rust {row.group(1)}, rust-toolchain.toml has {channel and channel.group(1)}")
    if pins != {row.group(2)}:
        problems.append(f"README says near-sdk ={row.group(2)}, Cargo.toml pins {sorted(pins)}")
    return problems


def check_readme_pin() -> list[str]:
    return readme_pin_problems(text("README.md"), text("contract/Cargo.toml"), text("contract/rust-toolchain.toml"))


def check_pin() -> list[str]:
    if not (ROOT / "docs/build.md").exists():
        return ["docs/build.md is missing"]
    return pin_problems(text("contract/Cargo.toml"), text("docs/build.md"))


def check_wasm_facts() -> list[str]:
    if not (ROOT / "docs/build.md").exists():
        return ["docs/build.md is missing"]
    return wasm_problems(text("docs/build.md"), text("docs/mainnet-proof.md"))


def check_snapshot() -> list[str]:
    return snapshot_problems("docs/security.md", text("docs/security.md"))


def check_anchors() -> list[str]:
    targets = {d: text(d) for d in doc_paths()}
    return [p for d in targets for p in anchor_gaps(d, targets[d], targets)]

CHECKS = [
    ("STATE.md and CHANGELOG.md match the git tags", check_versions),
    ("test counts in the docs match the code", check_test_counts),
    ("denial reasons in the README match the contract", check_denial_reasons),
    ("attack count in the docs matches the suite", check_attack_count),
    ("cost-limit defaults in the docs match the code", check_limits),
    ("every `make` target in the docs exists", check_make_targets),
    ("relative links in the docs resolve", check_links),
    ("no stale status phrases in the docs", check_stale),
    ("no private process terms in the docs", check_private),
    ("README lists every contract method", check_interface),
    ("unreleased methods stay out of the docs until their tag", check_unreleased),
    ("README lists every contract event", check_events),
    ("no dated balance in docs/security.md", check_snapshot),
    ("README network badge matches the proofs", check_badge),
    ("near-sdk pin is stated in docs/build.md", check_pin),
    ("README pinned build inputs match the toolchain files", check_readme_pin),
    ("artifact hashes in docs/build.md match the mainnet proof", check_wasm_facts),
    ("heading anchors in links exist", check_anchors),
]

if __name__ == "__main__":
    for name, check in CHECKS:
        report(name, check())
    print(f"\n{'Docs are stale: fix them before any push.' if failures else 'Docs match the code.'}")
    raise SystemExit(1 if failures else 0)
