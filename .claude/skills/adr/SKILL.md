---
name: adr
description: Write or update an Architecture Decision Record in docs/adr/ using MADR. Use when choosing a technology/library/model/protocol, deviating from the project brief, changing a module boundary or data store, or making a hard-to-reverse decision.
---

# ADR

1. Find the next number in `docs/adr/README.md` (planned numbers are reserved; use the planned one if it matches).
2. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-kebab-title.md`.
3. Fill: status (*Proposed*; deviations from the brief stay *Proposed* until the owner approves), date, brief reference, context, drivers, ≥ 2 considered options with pros/cons, decision, consequences (good and bad), confirmation (test/benchmark/CI check).
4. Verify every external fact (versions, limits, prices) against current docs and link them under "More information".
5. Update the index table in `docs/adr/README.md` and, if it implements a brief §3 decision, `docs/traceability.md`.
6. Describe the decision on its own merits; never cite external books.

## Checklist
- [ ] Options compared honestly, consequences include the bad
- [ ] Facts verified with links
- [ ] Index and traceability updated
- [ ] Status correct (Proposed until approved)
