# xype-ai

The XYPE AI extension: the client context engine (chunking, call graph, Merkle tree, scrubbing, encryption), the session client, and client-executed tools. Plan: `.claude/specs/phase-2/spec.md`.

## Public interface (so far)

There is no package entry point yet; it arrives with the second module (`src/index.ts` and `exports`).

| Export | Module | What |
|---|---|---|
| `LANGUAGE_IDS`, `LanguageId` | `src/context/grammars.ts` | The languages chunked by AST |
| `loadGrammar`, `createParser`, `grammarFile` | `src/context/grammars.ts` | Load a built grammar and get a parser for it |
| `GrammarError` | `src/context/grammars.ts` | A grammar could not be loaded |

## Grammars (ADR-0017)

The web-tree-sitter grammars are built from pinned sources listed in `grammars.json` into `grammars/`, which git ignores:

```bash
npm run grammars -w xype-ai
```

The first run lets tree-sitter-cli download its toolchain (wasi-sdk and binaryen, ≈ 700 MB) into the user cache. Later runs skip grammars that are up to date. Without built grammars, the grammar tests skip locally; CI builds them and requires them.

## Commands

```bash
npm test -w xype-ai      # vitest
npm run check            # from the repo root: format, lint, tsc, tests, duplication
```

Imports across folders inside `src/` go through the `@/` alias (ESLint `no-restricted-imports`).
