"""Secret and PII scrubbing with the shared rules in `packages/scrub-rules/rules.json`
(ADR-0019). The client scrubs whole files before chunking; the indexer re-checks each
decrypted chunk with the same rules and rejects a chunk that still has findings.

Matches become the placeholder plus the newlines they spanned, so line numbers and
line ranges stay valid. The placeholder never matches a rule, so scrubbing is
idempotent. Results carry counts per rule, never the matched text. Scrubbing is CPU
work: async callers run it in `asyncio.to_thread`.
"""

import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common_lib.security.errors import ScrubTimeoutError

# ASCII only: Python would otherwise fold some Unicode letters under IGNORECASE
# where JavaScript does not, and the two implementations must agree.
_FLAGS = re.ASCII
_HEX = re.compile(r"[0-9a-fA-F]+", _FLAGS)

type _Accept = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    pattern: re.Pattern[str]
    group: int
    # The rule runs only if the text contains one of these (lowercase when the rule
    # ignores case): cheap, and it keeps repeated rule prefixes from costing seconds.
    keywords: tuple[str, ...]
    case_insensitive: bool

    def applies_to(self, text: str, folded: str) -> bool:
        haystack = folded if self.case_insensitive else text
        return not self.keywords or any(k in haystack for k in self.keywords)


@dataclass(frozen=True, slots=True)
class _Threshold:
    min_length: int
    bits: float


@dataclass(frozen=True, slots=True)
class ScrubRules:
    placeholder: str
    budget_ms_per_mib: float
    min_budget_ms: float
    rules: tuple[_Rule, ...]
    entropy: _Rule
    hex_threshold: _Threshold
    other_thresholds: tuple[_Threshold, ...]
    other_requires: tuple[re.Pattern[str], ...]
    allow: tuple[re.Pattern[str], ...]


@dataclass(frozen=True, slots=True)
class ScrubResult:
    # Scrubbed, but still code: never shown in a repr or a log line.
    text: str = field(repr=False)
    findings: Mapping[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.findings


# reason: Any - one rule entry of the shared JSON file, validated by the tests.
def _rule(entry: Mapping[str, Any]) -> _Rule:
    insensitive = bool(entry.get("case_insensitive"))
    flags = _FLAGS | (re.IGNORECASE if insensitive else 0)
    keywords = tuple(k.lower() if insensitive else k for k in entry.get("keywords", ()))
    return _Rule(
        entry["id"],
        re.compile(entry["pattern"], flags),
        int(entry.get("secret_group", 0)),
        keywords,
        insensitive,
    )


def _thresholds(entries: list[Mapping[str, float]]) -> tuple[_Threshold, ...]:
    items = (_Threshold(int(t["min_length"]), float(t["threshold"])) for t in entries)
    return tuple(sorted(items, key=lambda t: t.min_length))


def load_rules(path: Path) -> ScrubRules:
    data = json.loads(path.read_text(encoding="utf-8"))
    entropy = data["entropy"]
    return ScrubRules(
        placeholder=data["placeholder"],
        budget_ms_per_mib=float(data["time_budget_ms_per_mib"]),
        min_budget_ms=float(data["min_time_budget_ms"]),
        rules=tuple(_rule(r) for r in data["rules"]),
        entropy=_rule({**entropy, "pattern": entropy["candidate"]}),
        hex_threshold=_Threshold(entropy["hex"]["min_length"], entropy["hex"]["threshold"]),
        other_thresholds=_thresholds(entropy["other"]),
        other_requires=tuple(re.compile(p, _FLAGS) for p in entropy["other_requires"]),
        allow=tuple(re.compile(p, _FLAGS) for p in entropy["allow"]),
    )


def shannon_entropy(token: str) -> float:
    """Bits per character of the token's own character distribution."""
    counts = Counter(token)
    return -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())


class SecretScrubber:
    def __init__(self, rules: ScrubRules) -> None:
        self._rules = rules

    def scrub(self, text: str) -> ScrubResult:
        """Raises `ScrubTimeoutError` when the text took longer than its budget."""
        started = time.perf_counter()
        mib = len(text.encode("utf-8")) / 2**20
        budget = max(self._rules.budget_ms_per_mib * mib, self._rules.min_budget_ms)
        findings: Counter[str] = Counter()
        # Keywords are checked on the input: a replacement can remove one, never add one.
        original, folded = text, text.lower()
        for rule in (*self._rules.rules, self._rules.entropy):
            if not rule.applies_to(original, folded):
                continue
            accept = self._is_high_entropy if rule is self._rules.entropy else _always
            text = self._replace(rule, text, findings, accept)
        if (time.perf_counter() - started) * 1000 > budget:
            raise ScrubTimeoutError("scrubbing exceeded its time budget")
        return ScrubResult(text, dict(findings))

    def _replace(self, rule: _Rule, text: str, findings: Counter[str], accept: _Accept) -> str:
        def substitute(match: re.Match[str]) -> str:
            secret = match.group(rule.group)
            if secret is None or not accept(secret):
                return match.group(0)
            findings[rule.rule_id] += 1
            offset = match.start(0)
            start, end = match.span(rule.group)
            whole = match.group(0)
            replaced = self._rules.placeholder + "\n" * secret.count("\n")
            return whole[: start - offset] + replaced + whole[end - offset :]

        return rule.pattern.sub(substitute, text)

    def _is_high_entropy(self, token: str) -> bool:
        if any(p.search(token) for p in self._rules.allow):
            return False
        if _HEX.fullmatch(token):
            limit: _Threshold | None = self._rules.hex_threshold
            if len(token) < self._rules.hex_threshold.min_length:
                return False
        elif not all(p.search(token) for p in self._rules.other_requires):
            # Identifiers and words ("DefaultEndpointsProtocol") are not keys.
            return False
        else:
            limit = next(
                (t for t in reversed(self._rules.other_thresholds) if len(token) >= t.min_length),
                None,
            )
        return limit is not None and shannon_entropy(token) >= limit.bits


def _always(_token: str) -> bool:
    return True
