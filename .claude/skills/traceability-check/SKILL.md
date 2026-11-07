---
name: traceability-check
description: Verify that every architecture decision in docs/PROJECT_BRIEF.md section 3 is mapped in docs/traceability.md to an ADR and to implementing code/tests, and that links are not stale. Use at the end of each phase and before phase reports.
---

# Traceability check

1. List every decision bullet in `docs/PROJECT_BRIEF.md` §3.1–§3.12 and the XYPE additions (§4, §5).
2. For each row in `docs/traceability.md`, check that:
   - the status is one of `planned (phase N)`, `in progress`, `implemented`, `deviation (ADR-NNNN)`;
   - every linked ADR exists and its status matches;
   - every linked code/test path exists (`Glob`) and, for `implemented`, a test covers it.
3. Report missing rows, broken links, and rows marked `implemented` without tests.
4. Propose the exact table edits; apply them only when asked.

## Checklist
- [ ] Every §3 decision has a row
- [ ] No broken ADR/code links
- [ ] Implemented rows have tests
