# 01 — Git

## Commit messages (Conventional Commits, enforced by `tools/check_commit_msg.py` in the commit-msg hook and in CI)
- Format: `type(scope): imperative summary` — header ≤ 72 chars, lowercase type, no trailing period.
- Body (optional) explains **why**. Footer `Refs: XYPE-123` when a Jira ticket exists.
- Breaking change: `type(scope)!: …` plus a `BREAKING CHANGE:` footer.
- Types: `feat`, `fix`, `chore`, `test`, `docs`, `refactor`, `perf`, `build`, `ci`, `style`, `revert`.
- Scopes: `gateway`, `orchestrator`, `router`, `llm`, `indexing`, `retrieval`, `cache`, `tasks`, `agents`, `connectors`, `storage`, `security`, `observability`, `evals`, `xype-ai`, `webview-ui`, `admin`, `fork`, `finetune`, `deploy`, `proto`, `design-tokens`, `claude`, `repo`, `deps`, `release`. Scope is optional for repo-wide changes.

## Commits
- Do keep each commit atomic: one logical change, tests in the same commit as the code they test.
- Never mix a refactor with a behaviour change in one commit.
- Never commit "WIP"/"misc fixes" to shared branches.
- Never commit secrets, real `.env` files, generated code (except lockfiles), large binaries or model weights.
- Never use `--no-verify`. Reason: hooks are the safety net; fix the failure instead.

## Branches
- Default branch is `master`; it is protected and always green.
- Names: `feat/XYPE-123-short-slug`, `fix/…`, `chore/…`, `test/…`, `docs/…`, `refactor/…`.
- Keep branches short-lived; rebase on `master` only with the owner's yes.
- Never force-push `master`.

## Pull requests
- Use `.github/pull_request_template.md` (what, why, how verified, screenshots for UI, risk, checklist).
- Squash-merge with a Conventional Commit title.

## Releases
- release-please generates versions per component and the CHANGELOG from commits (`release` skill).

## Human in the loop
- `git commit`, `push`, `merge`, `rebase`, `tag`, `reset`, `checkout --`, `branch -D` always ask (see `.claude/settings.json`). Use the `commit` skill.
