"""Validate Conventional Commit messages (rules: .claude/rules/01-git.md).

Usage:
    python tools/check_commit_msg.py .git/COMMIT_EDITMSG     # pre-commit commit-msg stage
    python tools/check_commit_msg.py --range origin/master..HEAD  # CI: every commit in a range
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TYPES = ("feat", "fix", "chore", "test", "docs", "refactor", "perf", "build", "ci", "style", "revert")
SCOPES = (
    "gateway", "orchestrator", "router", "llm", "indexing", "retrieval", "cache", "tasks", "agents",
    "connectors", "storage", "security", "observability", "evals", "xype-ai", "webview-ui", "admin",
    "fork", "finetune", "deploy", "proto", "design-tokens", "claude", "repo", "deps", "release",
)
MAX_HEADER = 72
HEADER = re.compile(r"^(?P<type>[a-z]+)(\((?P<scope>[a-z0-9-]+(,[a-z0-9-]+)*)\))?(?P<bang>!)?: (?P<subject>.+)$")
SKIP = re.compile(r"^(Merge |Revert \"|fixup! |squash! )")


def check(message: str) -> list[str]:
    lines = [ln for ln in message.splitlines() if not ln.startswith("#")]
    header = lines[0].strip() if lines else ""
    if SKIP.match(header):
        return []
    errors = []
    match = HEADER.match(header)
    if not match:
        return [f"header must be 'type(scope): summary', got: {header!r}"]
    if match["type"] not in TYPES:
        errors.append(f"unknown type {match['type']!r}; allowed: {', '.join(TYPES)}")
    for scope in (match["scope"] or "").split(","):
        if scope and scope not in SCOPES:
            errors.append(f"unknown scope {scope!r}; allowed: {', '.join(SCOPES)}")
    if len(header) > MAX_HEADER:
        errors.append(f"header is {len(header)} chars > {MAX_HEADER}")
    subject = match["subject"]
    if subject.endswith("."):
        errors.append("summary must not end with a period")
    if subject[:1].isupper():
        errors.append("summary must start lowercase (imperative mood)")
    if len(lines) > 1 and lines[1].strip():
        errors.append("leave a blank line between header and body")
    if match["bang"] and "BREAKING CHANGE:" not in message:
        errors.append("'!' requires a 'BREAKING CHANGE:' footer")
    return errors


def messages_in_range(rev_range: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x1e", rev_range], capture_output=True, text=True, check=True
    ).stdout
    pairs = []
    for record in out.split("\x1e"):
        if record.strip():
            sha, _, body = record.strip().partition("\x00")
            pairs.append((sha[:10], body))
    return pairs


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--range":
        targets = messages_in_range(argv[1])
    elif len(argv) == 1:
        targets = [("message", Path(argv[0]).read_text(encoding="utf-8"))]
    else:
        print(__doc__, file=sys.stderr)
        return 2
    failed = False
    for name, message in targets:
        for error in check(message):
            failed = True
            print(f"{name}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
