---
name: commit
description: Prepare a Conventional Commit for XYPE IDE - stage logically, write the message, run the reviewer subagent, show the summary and wait for the owner's explicit yes before committing. Use whenever work is ready to be committed.
disable-model-invocation: false
---

# Commit

1. `git status` and `git diff` — group changes into atomic logical commits (one concern each; tests with their code; never refactor + behaviour together).
2. Run the checks for the touched modules (lint, types, tests, `python tools/check_size.py`, `pre-commit run --files <files>`). Record the results.
3. Stage only the files for the first commit (`git add <paths>`, never `git add -A` blindly). Confirm no secrets, `.env`, generated code or binaries are staged.
4. Run the `reviewer` subagent on the staged diff (and `security-reviewer` if security/privacy-relevant).
5. Write the message: `type(scope): imperative summary` (≤ 72 chars), body = why, footer `Refs: XYPE-…` if any. Types/scopes: `.claude/rules/01-git.md`. End with the attribution trailer required by the session.
6. Present the summary (format in `.claude/rules/00-workflow.md`): message, files + reason, reused vs new, results, reviewer findings, risks.
7. **Stop. Wait for the owner's explicit yes.** Then `git commit` (never `--no-verify`, never `--amend` of pushed commits). If a hook fails, fix the cause and show the new summary.

## Checklist
- [ ] Atomic, tests included
- [ ] Checks run and passing (or failures stated)
- [ ] No secrets / generated files / binaries staged
- [ ] Reviewer findings included
- [ ] Owner said yes for this exact commit
