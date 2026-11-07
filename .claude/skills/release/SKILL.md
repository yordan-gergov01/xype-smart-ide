---
name: release
description: Cut a release of one or more XYPE IDE components with release-please (SemVer + generated CHANGELOG from Conventional Commits). Use when the owner asks to release.
disable-model-invocation: true
---

# Release

1. Releases are driven by release-please (`release-please-config.json`, `.release-please-manifest.json`): each component has its own version.
2. Check `master` is green and the release-please PR is up to date (`gh pr list --label "autorelease: pending"`).
3. Review the proposed versions and CHANGELOG entries with the owner: breaking changes (`!`) → major, `feat` → minor, `fix`/`perf` → patch.
4. Merging the release PR (owner's explicit yes) creates the tags and GitHub releases. Never create tags by hand.
5. After release: update `docs/plan.md` status if a phase milestone is reached.

## Checklist
- [ ] CI green on `master`
- [ ] Versions and CHANGELOG reviewed with the owner
- [ ] Owner approved the merge
