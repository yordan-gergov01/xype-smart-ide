# xype-ai

The XYPE AI extension: the client context engine (chunking, call graph, Merkle tree, scrubbing, encryption), the session client, and client-executed tools. Plan: `.claude/specs/phase-2/spec.md`.

## Public interface (so far)

The entry point is `src/index.ts` (`exports` in `package.json`). Everything there is pure and synchronous, with no `vscode` import, so the sync worker (slice 11) can run it off the extension host thread.

| Export | Module | What |
|---|---|---|
| `LANGUAGE_IDS`, `LanguageId` | `src/context/grammars.ts` | The languages chunked by AST |
| `loadGrammar`, `createParser`, `grammarFile` | `src/context/grammars.ts` | Load a built grammar and get a parser for it |
| `GrammarError` | `src/context/grammars.ts` | A grammar could not be loaded |
| `buildTree`, `innerHash`, `MerkleTree`, `MerkleError` | `src/context/merkle.ts` | The repository's Merkle tree with keyed ids (ADR-0018) |
| `parsePathRules`, `isDeniedPath`, `fileText` | `src/security/paths.ts` | Path exclusion, size, binary and UTF-8 checks; CRLF becomes LF (ADR-0019) |
| `parseRules`, `SecretScrubber`, `ScrubTimeoutError` | `src/security/scrubber.ts` | Whole-file scrubbing with the shared rules; fails closed past its budget |
| `hmacId`, `normalisePath` | `src/security/obfuscation.ts` | Obfuscated ids and the path form they hash (ADR-0016) |
| `clientKeyPair`, `wrappingKey`, `unwrapSessionKey`, `unwrapRepositoryKey` | `src/security/keys.ts` | The X25519 session-key exchange (ADR-0015) |
| `ClientCryptoBox` | `src/security/cryptoBox.ts` | Seals client-to-server and opens server-to-client payloads with its own counters |

A session key lives only in a `ClientCryptoBox`, in memory, and is never persisted: its counters restart at 1, so reusing a stored key would reuse nonces. Keys are `Uint8Array`s that callers wipe; JavaScript strings and OpenSSL's internal copies cannot be wiped. Everything follows the shared vectors in `packages/crypto-vectors` and the shared corpus in `packages/scrub-rules`.

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
