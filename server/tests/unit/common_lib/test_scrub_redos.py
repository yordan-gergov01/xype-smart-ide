"""ReDoS check of the shared scrub rules (ADR-0019, slice 8c1): every rule, alone, scans
each adversarial input of `packages/scrub-rules/adversarial.json` within its bound, and
every rule has inputs there, so a new rule cannot skip the check."""

import dataclasses
import itertools
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from common_lib.security import SecretScrubber, load_rules

RULES_DIR = Path(__file__).resolve().parents[4] / "packages" / "scrub-rules"
RULES = load_rules(RULES_DIR / "rules.json")
ADVERSARIAL = json.loads((RULES_DIR / "adversarial.json").read_text("utf-8"))
RULE_IDS = [r.rule_id for r in RULES.rules] + [RULES.entropy.rule_id]
# A keyword no input has: the entropy rule (which has none) is skipped this way.
_NEVER = "\x00never"


def only(rule_id: str) -> SecretScrubber:
    """A scrubber running just this rule, without a time budget of its own."""
    rules = tuple(r for r in RULES.rules if r.rule_id == rule_id)
    entropy = RULES.entropy
    if rule_id != entropy.rule_id:
        entropy = dataclasses.replace(entropy, keywords=(_NEVER,))
    return SecretScrubber(
        dataclasses.replace(RULES, rules=rules, entropy=entropy, budget_ms_per_mib=1e9)
    )


def inputs(rule_id: str) -> Iterator[str]:
    size = ADVERSARIAL["size_bytes"]
    for fragments, pump in itertools.product(
        ADVERSARIAL["triggers"][rule_id], ADVERSARIAL["pumps"]
    ):
        trigger = "".join(fragments)
        shapes = {
            "trigger_then_pump": trigger + pump * size,
            "trigger_and_40_pumps_repeated": (trigger + pump * 40) * (size // len(trigger)),
            "trigger_and_pump_repeated": (trigger + pump) * size,
        }
        for shape in ADVERSARIAL["shapes"]:
            yield shapes[shape][:size]


def test_every_rule_has_adversarial_inputs() -> None:
    assert set(ADVERSARIAL["triggers"]) == set(RULE_IDS)
    assert all(ADVERSARIAL["triggers"].values())


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_no_rule_backtracks_on_adversarial_input(rule_id: str) -> None:
    scrubber, bound = only(rule_id), ADVERSARIAL["max_seconds"]
    for text in inputs(rule_id):
        started = time.perf_counter()
        scrubber.scrub(text)
        elapsed = time.perf_counter() - started
        # The input is not shown: it may look like a secret to a log scanner.
        assert elapsed < bound, f"{rule_id}: {elapsed:.2f} s on a {len(text)}-byte input"


def test_the_check_catches_a_rule_that_backtracks() -> None:
    """A bounded rule of the portable subset can still be quadratic: this one is."""
    slow = dataclasses.replace(
        RULES.rules[0], rule_id="slow", pattern=re.compile("[a-z]{0,20000}x"), keywords=()
    )
    rules = dataclasses.replace(RULES, rules=(slow,), budget_ms_per_mib=1e9)
    entropy = dataclasses.replace(RULES.entropy, keywords=(_NEVER,))
    scrubber = SecretScrubber(dataclasses.replace(rules, entropy=entropy))
    started = time.perf_counter()
    scrubber.scrub("a" * ADVERSARIAL["size_bytes"])
    assert time.perf_counter() - started > ADVERSARIAL["max_seconds"]
