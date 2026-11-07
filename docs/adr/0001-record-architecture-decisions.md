# 0001 — Record architecture decisions with MADR

- Status: Accepted
- Date: 2026-11-07
- Brief reference: §2 Documentation, §10

## Context and problem statement

The brief requires an ADR for every significant decision and a traceability map from the architecture decisions in brief §3 to code. Decisions must survive across sessions and agents.

## Decision outcome

Use MADR files in `docs/adr/`, numbered `NNNN-kebab-title.md`, indexed in `docs/adr/README.md`. Any deviation from the brief is an ADR with status *Proposed* until the project owner approves it. `docs/traceability.md` (created in Phase 1) links each §3 decision to its ADR and code.

### Consequences

- Good: decisions are reviewable in PRs and searchable by agents.
- Bad: small overhead per decision; mitigated by the `adr` skill in Phase 1.

### Confirmation

The `traceability-check` skill (Phase 1) fails when a §3 decision has no ADR or code link.
