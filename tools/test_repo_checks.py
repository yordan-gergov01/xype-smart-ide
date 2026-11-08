"""Tests for the repo check scripts. Run: python -m unittest tools/test_repo_checks.py"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from check_commit_msg import check  # noqa: E402
from check_size import glob_to_regex, long_functions  # noqa: E402


class CommitMessageTests(unittest.TestCase):
    def test_valid_messages(self) -> None:
        for msg in (
            "feat(llm): add mock provider",
            "fix(gateway,security): reject expired tokens\n\nWhy: tokens outlived sessions.\n\nRefs: XYPE-12",
            "docs: describe local setup",
            "feat(proto)!: rename session stream\n\nBREAKING CHANGE: clients must regenerate stubs",
            "Merge branch 'master' into feat/x",
            "chore(release): release xype-ide 0.1.0",
        ):
            self.assertEqual(check(msg), [], msg)

    def test_invalid_messages(self) -> None:
        cases = {
            "added stuff": "header must be",
            "feature(llm): add x": "unknown type",
            "feat(payments): add x": "unknown scope",
            "feat(llm): Add x": "lowercase",
            "feat(llm): add x.": "period",
            "feat(llm): " + "x" * 80: "chars >",
            "feat(llm): add x\nbody without blank line": "blank line",
            "feat(llm)!: drop api": "BREAKING CHANGE",
        }
        for msg, expected in cases.items():
            errors = check(msg)
            self.assertTrue(any(expected in e for e in errors), f"{msg!r} -> {errors}")


class SizeCheckTests(unittest.TestCase):
    def test_glob_rules(self) -> None:
        self.assertTrue(glob_to_regex("**/gen/**").match("server/gen/a/b.py"))
        self.assertTrue(glob_to_regex("**/gen/**").match("gen/x.ts"))
        self.assertTrue(glob_to_regex("**/*_pb2.py").match("a/b/c_pb2.py"))
        self.assertFalse(glob_to_regex("src/*.ts").match("src/a/b.ts"))

    def test_long_functions(self) -> None:
        body = "\n".join(["def f():"] + ["    x = 1"] * 50)
        self.assertEqual([(n, ln) for n, ln, _ in long_functions(body, 50)], [("f", 1)])
        self.assertEqual(long_functions("def g():\n    return 1\n", 50), [])


if __name__ == "__main__":
    unittest.main()
