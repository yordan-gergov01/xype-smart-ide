"""Fail when tracked configuration enables a paid LLM provider (ADR-0005, ADR-0012).

Paid models stay off until the owner approves Phase 10. This checks the files that
configure services: env templates, compose files and CI workflows. It reads only files
tracked by git, so a real `.env` (git-ignored) is never opened.

Refused, case-insensitively, with or without `export`:
- PAID_MODELS_ENABLED set to anything but an explicit false value;
- a paid provider key (OPENROUTER_API_KEY, DEEPSEEK_API_KEY) with any value;
- LLM_PRIMARY or LLM_FALLBACK naming a paid provider (`deepseek`, `openrouter:<model>`);
- for these keys, a CI expression (`${{ secrets.X }}`) or a compose default
  (`${VAR:-true}`) that would enable them. A bare `${VAR}` pass-through is wiring.
Limits: one assignment per line at its start; assignments inside a command line
(`docker run -e KEY=value`), YAML flow collections, block scalars and quoted compose
defaults are not parsed.

Usage: python tools/check_paid_config.py
"""

from __future__ import annotations

import re
import sys

from check_size import ROOT, git_files, glob_to_regex

CONFIG_FILES = tuple(
    glob_to_regex(g)
    for g in (
        "**/.env.example",
        "**/*.env.example",
        "deploy/**/*.yml",
        "deploy/**/*.yaml",
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
    )
)
# KEY=value (env files, compose list form) or KEY: value (YAML maps).
ASSIGNMENT = re.compile(
    r"""^\s*(?:-\s*)?(?:export\s+)?["']?(?P<key>[A-Za-z][A-Za-z0-9_]*)["']?\s*[:=]\s*["']?"""
    r"""(?P<value>[^"'\s#]*)""",
    re.MULTILINE,
)
# ${VAR}, ${VAR:-default}, ${VAR-default}: the default is what a missing variable gives.
SUBSTITUTION = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::?[-=](?P<default>[^}]*))?\}$")
PAID_PROVIDERS = {"openrouter", "deepseek"}
PAID_KEYS = {"OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"}
PROVIDER_KEYS = {"LLM_PRIMARY", "LLM_FALLBACK"}
GUARDED_KEYS = PAID_KEYS | PROVIDER_KEYS | {"PAID_MODELS_ENABLED"}
FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}
EMPTY_VALUES = {"", "~", "null"}


def refusal(key: str, value: str) -> str | None:
    key = key.upper()
    if key not in GUARDED_KEYS:
        return None
    if value.startswith("${{"):
        return "takes its value from CI secrets or variables"
    substitution = SUBSTITUTION.match(value)
    if substitution:
        value = substitution["default"] or ""
    if value.lower() in EMPTY_VALUES:
        return None
    if key == "PAID_MODELS_ENABLED":
        return None if value.lower() in FALSE_VALUES else "enables paid models"
    if key in PAID_KEYS:
        return "sets a paid provider key"
    if value.lower().split(":")[0] in PAID_PROVIDERS:
        return "selects a paid provider"
    return None


def findings(rel: str, text: str) -> list[str]:
    problems = []
    for match in ASSIGNMENT.finditer(text):
        reason = refusal(match["key"], match["value"])
        if reason:
            line = text.count("\n", 0, match.start("key")) + 1
            problems.append(f"{rel}:{line}: {match['key']} {reason} (Phase 10 approval needed)")
    return problems


def main() -> int:
    files = [f for f in git_files("ls-files") if any(p.match(f) for p in CONFIG_FILES)]
    problems = [
        problem
        for f in files
        # A tracked symlink could point at a real .env: never follow one.
        if (ROOT / f).is_file() and not (ROOT / f).is_symlink()
        for problem in findings(f, (ROOT / f).read_text(encoding="utf-8"))
    ]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
