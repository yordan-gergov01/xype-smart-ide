---
paths:
  - "clients/**/*.{ts,tsx}"
  - "packages/**/*.{ts,tsx}"
---

# 05 — TypeScript and React

## TypeScript
- Do keep `strict: true`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`. Never use `any`; use `unknown` and narrow.
- Do define module interfaces as `interface` and export them from the package entry point only.
- Do use `readonly` for data that is not mutated; prefer discriminated unions over boolean flags.
- TypeScript is pinned to 6.0.x until typescript-eslint supports 7 (ADR-0008).

## React + Vite (webviews, admin dashboard)
- Do use function components and hooks; keep components small and presentational, logic in hooks.
- Do take all colors, fonts, radius and spacing from `packages/design-tokens`. Never hard-code brand values.
- Do make UI keyboard-first and accessible (labels, focus order, contrast).

## VS Code extension (`clients/xype-ai`)
- Do keep heavy work (hashing, chunking) off the extension host thread: worker threads, debounced (> 2 s idle), CPU-capped.
- Do scrub secrets, then encrypt, before anything leaves the client.
- Do communicate with webviews only through typed messages (one shared message type module).
- Never execute a tool that edits code or runs commands without the approval rules in `ApprovalPolicy`.

## Testing
- vitest for unit tests; Playwright for UI e2e.
