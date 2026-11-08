---
name: open-pr
description: Open a GitHub pull request for the current XYPE IDE branch using the repo PR template, after the owner approves pushing. Use when a branch is ready for review.
disable-model-invocation: true
---

# Open PR

1. Confirm the branch is not `master`, all commits are Conventional, and checks pass locally.
2. Ask the owner before `git push -u origin <branch>` (push always needs an explicit yes).
3. Fill `.github/pull_request_template.md`: what, why, how verified (commands + results), screenshots for UI, risk level, checklist.
4. Title = Conventional Commit summary (it becomes the squash-merge commit).
5. `gh pr create --base master --title "<title>" --body-file <tmp file>`; end the body with the session's PR attribution line.
6. Report the PR URL. Never enable auto-merge unless asked.

## Checklist
- [ ] Owner approved the push
- [ ] Template fully filled, verification is concrete
- [ ] Title is a valid Conventional Commit
