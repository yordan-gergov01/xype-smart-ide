# 00 — Workflow

Loop: **explore → plan → implement → verify → summarize → confirm → commit.**

## Explore (reuse first)
- Do read the target module's README and interface before changing it. Reason: the interface is the contract.
- Do search the repo for an existing helper, pattern or template before writing new code, and name what you reused in the summary. Reason: no duplicates.

## Plan
- Do write a short plan before any change that touches more than one module or more than ~3 files.
- Do use the `grilling` skill (one question at a time) when requirements are ambiguous or the task is large.
- Do write or update a spec in `.claude/specs/<feature>/spec.md` for features. Reason: the spec is what reviewers check against.
- Do stop and propose an ADR when the brief conflicts with reality. Never deviate silently.

## Implement
- Do work in small vertical slices, test-first where practical (`tdd` skill).
- Never edit generated code, lockfiles by hand, or upstream Code-OSS sources outside `fork/patches/`.

## Verify
- Do run lint, types, tests for the affected modules, the size and duplication checks, and relevant evals.
- Never claim something works without having run it. Say "not run" when you could not run it.

## Summarize and wait (mandatory before every commit)
Present, in this order:
1. Proposed commit message(s).
2. Files changed, one line of reason each.
3. Reused vs newly written.
4. Test and check results (with numbers).
5. `reviewer` subagent findings on the staged diff.
6. Risks and follow-ups.

Then **stop and wait for the owner's explicit yes**. Never commit, push, merge, rebase, tag, reset or delete branches without it. An approval covers only the commit it was given for.

## Keep context clean
- Do use subagents for broad searches and for independent review.
- Do write `.claude/specs/<feature>/handoff.md` (`handoff` skill) when a task spans sessions.
