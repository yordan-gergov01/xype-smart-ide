## What

<!-- One or two sentences. Title must be a Conventional Commit: type(scope): summary -->

## Why

<!-- Problem, ticket (Refs: XYPE-123), spec or ADR link. -->

## How verified

<!-- Exact commands and results (lint, types, tests with counts/coverage, evals, compose). Say "not run" for anything you could not run. -->

## Screenshots (UI changes)

## Risk

- Level: low / medium / high
- Rollback:

## Checklist

- [ ] Conventional Commit title; atomic commits
- [ ] Lint, types, tests pass for touched modules (coverage ≥ 85% on core)
- [ ] No file > 500 lines, no function > 50 lines, no new duplication
- [ ] Import boundaries respected; adapters wired only in composition roots
- [ ] No plaintext code/bodies in storage, logs or traces; no secrets committed
- [ ] No paid API calls (mock or local models only)
- [ ] Docs, ADRs, glossary and `docs/traceability.md` updated
- [ ] Runs in docker compose
