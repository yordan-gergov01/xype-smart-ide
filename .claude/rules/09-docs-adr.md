# 09 — Docs and ADRs

## When an ADR is required (MADR, `docs/adr/`, `adr` skill)
- Choosing or replacing a technology, library, model or protocol.
- Any deviation from `docs/PROJECT_BRIEF.md`.
- A new module boundary, data-store change, or privacy/security-relevant design.
- A decision that is expensive to reverse.
New ADRs start as *Proposed*; deviations stay *Proposed* until the owner approves.

## Always update in the same commit
- `docs/traceability.md` when work implements or changes a brief §3 decision (`traceability-check` skill).
- `docs/glossary.md` when a new domain term appears in code, specs or commits.
- The module README when its public interface changes.
- Runbooks in `docs/runbooks/` when operating procedures change.

## Style
- Docs are English, concise, checkable. Prefer tables and lists over prose.
- Never mention or cite external books in the repo; describe decisions on their own merits.
- Keep `CLAUDE.md` under ~120 lines; details go into rules, docs and skills.
