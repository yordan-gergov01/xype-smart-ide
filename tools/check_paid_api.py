"""Fail when a paid LLM API endpoint appears outside the allowed places.

Paid endpoints are listed in tools/paid_api_hosts.txt. They may appear only in docs, in the guard
tooling itself, and (from Phase 1 slice 6) in the disabled paid adapter and its recorded fixtures.

Usage: python tools/check_paid_api.py [FILE...]   (no args = all tracked + untracked files)
"""

from __future__ import annotations

import sys

from check_size import ROOT, candidate_files, glob_to_regex

HOSTS_FILE = ROOT / "tools" / "paid_api_hosts.txt"
ALLOWED = tuple(
    glob_to_regex(g)
    for g in (
        "docs/**",
        "tools/paid_api_hosts.txt",
        "tools/check_paid_api.py",
        ".claude/settings.json",
        ".claude/hooks/**",
        ".claude/rules/**",
        ".claude/agents/**",
        "CLAUDE.md",
    )
)


def load_hosts() -> list[str]:
    lines = HOSTS_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip().lower() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def findings(rel: str, hosts: list[str]) -> list[str]:
    if any(p.match(rel) for p in ALLOWED):
        return []
    path = ROOT / rel
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return []
    try:
        text = path.read_text(encoding="utf-8").lower()
    except UnicodeDecodeError:
        return []
    return [f"{rel}: references paid API endpoint '{h}'" for h in hosts if h in text]


def main(argv: list[str]) -> int:
    hosts = load_hosts()
    files = sorted({f.replace("\\", "/") for f in candidate_files(argv)})
    problems = [p for f in files for p in findings(f, hosts)]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
