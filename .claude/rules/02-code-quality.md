# 02 — Code quality

## No unnecessary code (YAGNI)
- Do implement only what the current spec needs.
- Never add speculative abstractions, options or "future-proof" layers.
- Do delete dead code; never comment it out.

## Reuse, don't duplicate
- Do search for an existing function before adding one.
- Do extract shared logic only when it is genuinely the same concept (rule of three), into the owning module, behind its interface.
- jscpd runs in pre-commit/CI with a low threshold; new duplicates fail the build.

## Size limits (enforced by hook, pre-commit and CI)
- No source file over **500 lines**. Exemptions live only in `tools/size_limits.toml`.
- No function over **50 lines**; cyclomatic complexity ≤ 10 (ruff `C901`, ESLint `complexity`).
- When a limit is hit, split by responsibility, not arbitrarily.

## Naming and comments
- Do use terms from `docs/glossary.md`; add new domain terms there in the same commit.
- Comments explain **why**, never **what**.

## Errors
- Do raise typed domain errors (subclasses of the module's base error).
- Never use bare `except` or swallow errors; never `except Exception: pass`.
- Do fail fast at boundaries (validate input at API/gRPC/queue edges).

## Dependencies
- Do add a dependency only with a one-line reason in the commit summary.
- Do prefer the standard library and existing dependencies.
- Do pin exact versions (lockfiles committed).
