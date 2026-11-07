---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

<!-- Vendored from mattpocock/skills@4ae2317ddb22 (skills/productivity/handoff/SKILL.md), MIT License, (c) 2026 Matt Pocock. Adapted for XYPE IDE; see ../THIRD_PARTY_NOTICES.md. -->

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to `.claude/specs/<feature>/handoff.md` (create the folder if needed).

Include a "suggested skills" section in the document, naming which skills the next agent should call the Skill tool for.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
