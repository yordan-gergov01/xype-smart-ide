# xype-ai

The XYPE AI extension: the client context engine (chunking, call graph, Merkle tree, scrubbing, encryption), the session client, and client-executed tools. Plan: `.claude/specs/phase-2/spec.md`.

## Public interface (so far)

The entry point is `src/index.ts` (`exports` in `package.json`). Nothing there imports `vscode`, so the sync can run off the extension host thread. Everything is pure and synchronous except the walker (file system) and the scrub worker (a worker thread).

| Export | Module | What |
|---|---|---|
| `LANGUAGE_IDS`, `LanguageId` | `src/context/grammars.ts` | The languages chunked by AST |
| `loadGrammar`, `createParser`, `grammarFile` | `src/context/grammars.ts` | Load a built grammar and get a parser for it |
| `GrammarError` | `src/context/grammars.ts` | A grammar could not be loaded |
| `loadParsers`, `chunkFile`, `languageOf`, `Chunk`, `PARSE_BUDGET_MS` | `src/context/chunker.ts` | Chunks of a scrubbed file: by syntax tree for the 7 languages, line windows otherwise or past the parse budget; `loadParsers` reports grammars that failed (ADR-0017) |
| `buildCallGraph`, `CallGraph`, `ChunkKey` | `src/context/callGraph.ts` | The chunks that call a chunk (its dependents), client-side |
| `buildTree`, `innerHash`, `MerkleTree`, `MerkleError` | `src/context/merkle.ts` | The repository's Merkle tree with keyed ids, and each leaf's chunk (ADR-0018) |
| `parsePathRules`, `isDeniedPath`, `fileText` | `src/security/paths.ts` | Path exclusion, size, binary and UTF-8 checks; CRLF becomes LF (ADR-0019) |
| `parseRules`, `SecretScrubber`, `ScrubTimeoutError`, `ScrubbedText` | `src/security/scrubber.ts` | Whole-file scrubbing with the shared rules; fails closed past its budget. `ScrubbedText` is the only text chunking and sealing accept |
| `ScrubWorker`, `WorkerScrub` | `src/security/scrubWorker.ts` | Scrubbing in a worker thread, terminated past a hard limit (the file is then excluded) |
| `walkRepository`, `WalkedFile` | `src/sync/walker.ts` | A repository's files after `.gitignore`, `.xype/ignore`, `denied_paths` and size checks; never follows links |
| `MerkleResponder`, sync message types | `src/sync/responder.ts`, `messages.ts` | Answers `MerkleSyncRequest`s and seals the chunks a `MerkleDelta` asks for |
| `AuthApi`, `TokenSource` | `src/session/api.ts`, `tokens.ts` | Login, refresh, `/me` and session keys over REST with the server's CA; a refresh token is used once, a failed refresh logs in again |
| `mintSessionKey` | `src/session/mint.ts` | A session key per stream in a new `ClientCryptoBox`, and the repositories' HMAC keys |
| `SessionClient` | `src/session/session.ts` | The `Session` stream: Hello, heartbeats, the Merkle sync, token renewal, one key re-mint, backoff |
| `grpcTransport`, `sessionTransport` | `src/session/transport.ts` | gRPC over HTTP/2 (Connect-ES, ADR-0038) behind the `SessionTransport` seam |
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

## Generated code (ADR-0038)

`src/gen/` holds the TypeScript generated from `proto/` and is not committed. `npm run check` and the pre-commit hook regenerate it; on its own:

```bash
npm run gen
```

## Commands

```bash
npm test -w xype-ai      # vitest
npm run check            # from the repo root: format, lint, tsc, tests, duplication
```

Imports across folders inside `src/` go through the `@/` alias (ESLint `no-restricted-imports`).
