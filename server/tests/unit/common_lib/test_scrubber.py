"""The scrubber against the shared corpus, and the shared rules against the portable,
bounded regex subset of ADR-0019 (the TypeScript client runs the same corpus)."""

import base64
import dataclasses
import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from common_lib.security import ScrubTimeoutError, SecretScrubber, load_rules

RULES_DIR = Path(__file__).resolve().parents[4] / "packages" / "scrub-rules"
RULES = load_rules(RULES_DIR / "rules.json")
# Inputs are stored as short fragments, so secret scanners never see a whole token.
CASES = [
    {**c, "input": "".join(c["input"]), "expected": "".join(c["expected"])}
    for c in json.loads((RULES_DIR / "corpus.json").read_text("utf-8"))["cases"]
]
SCRUBBER = SecretScrubber(RULES)


def all_patterns() -> list[str]:
    data = json.loads((RULES_DIR / "rules.json").read_text("utf-8"))
    entropy = data["entropy"]
    return [
        *(r["pattern"] for r in data["rules"]),
        entropy["candidate"],
        *entropy["allow"],
        *entropy["other_requires"],
    ]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus(case: dict[str, object]) -> None:
    result = SCRUBBER.scrub(str(case["input"]))
    assert result.text == case["expected"]
    assert dict(result.findings) == case["findings"]
    assert result.clean == (not case["findings"])


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_is_idempotent_and_keeps_lines(case: dict[str, object]) -> None:
    once = SCRUBBER.scrub(str(case["input"]))
    assert once.text.count("\n") == str(case["input"]).count("\n")
    twice = SCRUBBER.scrub(once.text)
    assert twice.text == once.text
    assert twice.clean


# Split so secret scanners do not read the test itself as a key.
BEGIN = "-----BEGIN RSA " + "PRIVATE KEY-----\n"
MIB = 2**20


@pytest.mark.parametrize(
    "text",
    [
        BEGIN + "A" * MIB,
        (BEGIN * (MIB // len(BEGIN)))[:MIB],
        ("eyJ" * MIB)[:MIB],
        "a" * MIB,
        "password = " + "x" * MIB,
        ("x@" * MIB)[:MIB],
        ("+1" * MIB)[:MIB],
    ],
    ids=[
        "unterminated-key",
        "repeated-key-header",
        "repeated-jwt-prefix",
        "one-letter",
        "long-assignment",
        "at-signs",
        "plus-digits",
    ],
)
def test_pathological_input_is_linear(text: str) -> None:
    """1 MiB inputs; a generous budget here, so a slow runner measures, not flakes."""
    generous = SecretScrubber(dataclasses.replace(RULES, budget_ms_per_mib=60_000.0))
    started = time.perf_counter()
    generous.scrub(text)
    assert time.perf_counter() - started < 5.0


def test_budget_is_measured_on_the_input() -> None:
    """A file whose long secrets shrink to the placeholder keeps the budget of its input.

    The budget below is about 6 s for the 200 KiB input but about 30 ms for the 1 KiB
    output, so a budget taken from the output would fail here even on a fast machine.
    """
    tokens = (base64.b64encode(hashlib.sha512(str(i).encode()).digest() * 50) for i in range(50))
    blob = "".join(f"k = '{t.decode()[:4000]}'\n" for t in tokens)
    rules = dataclasses.replace(RULES, budget_ms_per_mib=30_000.0, min_budget_ms=1.0)
    result = SecretScrubber(rules).scrub(blob)
    assert result.findings == {"high-entropy": 50}
    assert len(result.text) < len(blob) // 50


def test_fails_closed_when_over_budget() -> None:
    no_budget = SecretScrubber(
        dataclasses.replace(RULES, budget_ms_per_mib=0.0, min_budget_ms=-1.0)
    )
    with pytest.raises(ScrubTimeoutError):
        no_budget.scrub("x = 1\n")


def test_result_repr_hides_the_text() -> None:
    assert "hunter2" not in repr(SCRUBBER.scrub("note = 'hunter2'\n"))


def test_keywords_are_literal_parts_of_their_patterns() -> None:
    """A keyword prefilter is only sound if every match must contain the keyword."""
    data = json.loads((RULES_DIR / "rules.json").read_text("utf-8"))
    for rule in data["rules"]:
        for keyword in rule.get("keywords", []):
            assert re.escape(keyword).replace("\\-", "-") in rule["pattern"], rule["id"]


# The portable subset: features that mean the same in Python `re` and JavaScript, and
# no unbounded or overlapping repetition (ADR-0019).
_FORBIDDEN_ESCAPES = set("wWsSdDbB123456789")
_ADJACENT_CLASSES = re.compile(r"\[((?:\\.|[^\]\\])+)\]\{\d+,\d+\}\[((?:\\.|[^\]\\])+)\]\{")
_ESCAPED_CHARS = {"\\t": "\t", "\\r": "\r", "\\n": "\n"}


def _class_chars(body: str) -> set[str]:
    chars: set[str] = set()
    items = re.findall(r"\\.|.", body)
    index = 0
    while index < len(items):
        char = _ESCAPED_CHARS.get(items[index], items[index][-1])
        if index + 2 < len(items) and items[index + 1] == "-":
            chars |= {chr(c) for c in range(ord(char), ord(items[index + 2][-1]) + 1)}
            index += 3
            continue
        chars.add(char)
        index += 1
    return chars


def _outside_class(pattern: str) -> list[tuple[int, str, bool]]:
    """(index, char, escaped) for every character outside a character class."""
    tokens: list[tuple[int, str, bool]] = []
    in_class = escaped = False
    for index, char in enumerate(pattern):
        if escaped:
            if not in_class:
                tokens.append((index, char, True))
            escaped = False
        elif char == "\\":
            escaped = True
        elif in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        else:
            tokens.append((index, char, False))
    return tokens


def _token_problems(pattern: str) -> list[str]:
    problems: list[str] = []
    for index, char, escaped in _outside_class(pattern):
        if escaped and char in _FORBIDDEN_ESCAPES:
            problems.append(f"escape \\{char}")
        elif not escaped and char in "*+.":
            problems.append(f"unbounded or wildcard {char!r}")
        elif (
            not escaped
            and pattern[index : index + 2] == "(?"
            and pattern[index + 2 : index + 3] not in {":", "!", "="}
        ):
            problems.append("group other than (?: (?! (?=")
    return problems


def portability_problems(pattern: str) -> list[str]:
    problems = _token_problems(pattern)
    if re.search(r"\{\d+,\}", pattern):
        problems.append("open-ended repetition")
    for left, right in _ADJACENT_CLASSES.findall(pattern):
        negated = left.startswith("^") or right.startswith("^")
        if not negated and _class_chars(left) & _class_chars(right):
            problems.append(f"overlapping adjacent classes [{left}] [{right}]")
    return problems


@pytest.mark.parametrize("pattern", all_patterns())
def test_rules_use_the_portable_subset(pattern: str) -> None:
    assert portability_problems(pattern) == []


@pytest.mark.parametrize(
    "pattern",
    [r"\w+", "a.b", "(?<=x)y", "(?i)k", "[a-z]{0,9}[a-c]{1,2}", "x{2,}", r"(a)\1"],
)
def test_the_subset_check_catches(pattern: str) -> None:
    assert portability_problems(pattern) != []


def test_rule_ids_are_unique_and_stable() -> None:
    ids = [r.rule_id for r in RULES.rules] + [RULES.entropy.rule_id]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9-]+", i) for i in ids)
