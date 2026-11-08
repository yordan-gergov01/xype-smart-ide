# 0008 — TypeScript tooling: npm workspaces, TypeScript 6.0, ESLint import rules

- Status: Accepted (npm chosen by the project owner on 2026-11-07)
- Date: 2026-11-07
- Deciders: project owner
- Brief reference: §1, §8

## Context and problem statement

The repo holds the VS Code extension, two React apps and a design-token package. They need one install, shared lint and type settings, and import boundaries. XYPE's template_web uses npm with exact versions.

## Decision drivers

- Same package manager as template_web; nothing extra to install next to Node 24.
- Reproducible installs.
- Boundaries checked by tools.

## Considered options

1. **npm workspaces**, exact versions, one committed `package-lock.json`.
2. pnpm workspaces.

## Decision outcome

Chosen option: **1**.

- Root `package.json`: `workspaces: ["clients/*", "packages/*"]`, `engines.node >=24`, devDependencies pinned exactly (`.npmrc`: `save-exact=true`, `engine-strict=true`). CI installs with `npm ci`.
- One script runs everything: `npm run check` = Prettier check, ESLint, `tsc`, tests, jscpd.
- TypeScript **6.0.3**, strict (`tsconfig.base.json`). TypeScript 7 is out, but typescript-eslint 8.72.1 supports `<6.1` only; move when it does.
- ESLint 10 flat config with `typescript-eslint` strict type-checked rules and the limits from rule 02: `complexity` 10, `max-lines` 500, `max-lines-per-function` 50.
- Import boundaries use ESLint's built-in `no-restricted-imports` with the `@/` alias, as in template_web, so no boundaries plugin is added.
- The repo's own Node scripts (`.claude/hooks/*.mjs`) are linted and type-checked with `checkJs` and JSDoc types.
- Prettier: single quotes, width 100. Markdown and YAML are not reformatted.
- vitest is added with the first TypeScript package (`npm test` runs the hook tests until then).

### Consequences

- Good: one install and one check command; versions visible in `package.json`.
- Good: no extra ESLint plugin for boundaries.
- Bad: npm workspaces are less strict than pnpm about undeclared dependencies; ESLint and review must catch them.
- Bad: `npm audit` reports a moderate advisory in `minimatch` under ESLint 10 (dev-only, no fixed release yet); recheck on the next ESLint update.

### Confirmation

CI job `node` runs `npm ci && npm run check`.

## Pros and cons of the options

### pnpm

- Good: strict dependency isolation, fast.
- Bad: one more tool; corepack failed on this machine (EXDEV); differs from template_web.

## More information

- template_web `docs/DECISIONS.md` D-002 (same approach, TypeScript 5.9 there).
