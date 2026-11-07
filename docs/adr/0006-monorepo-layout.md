# 0006 — Monorepo layout and Phase 0 drafts location

- Status: Proposed
- Date: 2026-11-07
- Brief reference: §8

## Context and problem statement

The brief gives a target layout and allows changes with an ADR. Phase 0 also produces proto and schema drafts before the tooling (`buf`, Alembic) exists.

## Decision outcome

- Keep the brief's layout (§8) as-is for Phase 1. Two clarifications:
  - `server/` is a **uv workspace**; each module (`gateway`, `orchestrator`, …) is its own package `xype_<module>` with `src/` layout, so import-linter contracts are per package.
  - `clients/` and `packages/` form a **pnpm workspace**.
- Phase 0 drafts live in `docs/drafts/` (`xype_v1.proto`, `schema.sql`). Phase 1 moves them to `proto/xype/v1/` and the first Alembic revision, so `buf breaking` and migrations start from a reviewed baseline. The drafts are then deleted (no duplicate sources of truth).

### Consequences

- Good: the reviewed draft becomes the baseline without history noise.
- Bad: one move step in Phase 1.

### Confirmation

Phase 1 CI: `buf lint` passes on `proto/`; Alembic `upgrade head` on an empty Postgres produces the schema in the draft.
