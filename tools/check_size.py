"""Enforce source-size limits: max lines per file and per Python function.

Usage:
    python tools/check_size.py            # all tracked + untracked source files
    python tools/check_size.py FILE...    # only these files (pre-commit passes staged files)
    python tools/check_size.py --changed  # files changed vs HEAD (used by the Stop hook)

Limits and exemptions live only in tools/size_limits.toml.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tools" / "size_limits.toml"


@dataclass(frozen=True, slots=True)
class Limits:
    max_file_lines: int
    max_function_lines: int
    extensions: frozenset[str]
    exempt: tuple[re.Pattern[str], ...]


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate the documented glob subset into an anchored regex (same rules as the Node hook)."""
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def load_limits() -> Limits:
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    return Limits(
        max_file_lines=int(data["max_file_lines"]),
        max_function_lines=int(data["max_function_lines"]),
        extensions=frozenset(data["source_extensions"]),
        exempt=tuple(glob_to_regex(g) for g in data["exempt"]),
    )


def git_files(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def candidate_files(argv: list[str]) -> list[str]:
    if argv == ["--changed"]:
        changed = git_files("diff", "--name-only", "HEAD")
        return changed + git_files("ls-files", "--others", "--exclude-standard")
    if argv:
        return argv
    return git_files("ls-files") + git_files("ls-files", "--others", "--exclude-standard")


def is_checked(rel: str, limits: Limits) -> bool:
    if Path(rel).suffix not in limits.extensions:
        return False
    return not any(p.match(rel) for p in limits.exempt)


def long_functions(source: str, max_lines: int) -> list[tuple[str, int, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # syntax errors are reported by ruff, not here
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > max_lines:
                found.append((node.name, node.lineno, length))
    return found


def check_file(rel: str, limits: Limits) -> list[str]:
    path = ROOT / rel
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    problems = []
    n_lines = len(text.splitlines())
    if n_lines > limits.max_file_lines:
        problems.append(f"{rel}: {n_lines} lines > {limits.max_file_lines} (split by responsibility)")
    if path.suffix == ".py":
        for name, line, length in long_functions(text, limits.max_function_lines):
            problems.append(
                f"{rel}:{line}: function '{name}' is {length} lines > {limits.max_function_lines}"
            )
    return problems


def main(argv: list[str]) -> int:
    limits = load_limits()
    files = [f.replace("\\", "/") for f in candidate_files(argv)]
    problems = [p for f in sorted(set(files)) if is_checked(f, limits) for p in check_file(f, limits)]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
