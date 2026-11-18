"""Contract of `Scanner` (ADR-0019): the in-process fake and the scan processes give the
same verdict for a clean chunk, a planted secret and text that is not UTF-8."""

import json
from pathlib import Path

from indexer.scan import Scanner

RULES_DIR = Path(__file__).resolve().parents[4] / "packages" / "scrub-rules"
RULES_FILE = RULES_DIR / "rules.json"
CLEAN = b"def add(a, b):\n    return a + b\n"


def planted_secret() -> bytes:
    """A corpus input the rules must change; stored there as fragments."""
    corpus = json.loads((RULES_DIR / "corpus.json").read_text("utf-8"))
    for case in corpus["cases"]:
        text, expected = "".join(case["input"]), "".join(case["expected"])
        if text != expected:
            return text.encode()
    raise AssertionError("the corpus has no case with a finding")


async def check_verdicts(scanner: Scanner) -> None:
    assert await scanner.scan(CLEAN) == "clean"
    assert await scanner.scan(planted_secret()) == "secret_found"
    assert await scanner.scan(bytes([0xFF, 0xFE]) + b" not utf-8") == "unscannable"
    assert await scanner.scan(b"") == "clean"
