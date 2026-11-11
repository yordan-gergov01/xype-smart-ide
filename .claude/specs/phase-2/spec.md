# Spec: Phase 2 — indexing and context

Plan: `docs/plan.md` (Phase 2). Brief: §3.3, §3.4.2, §3.4.3, §3.4.7, §3.5 (context
awareness, Merkle sync), §3.7, §9 Phase 2. Decisions: ADR-0002, ADR-0006, ADR-0012,
ADR-0013, ADR-0034, ADR-0035; new ADR-0014 … ADR-0020 (Proposed).

## Goal

The `xype-ai` extension chunks a workspace, scrubs secrets, encrypts and obfuscates the
chunks, and syncs them over the authenticated gRPC `Session` stream. The server embeds
them statelessly and keeps only vectors and obfuscated metadata, with Merkle delta sync
on later changes. A privacy suite proves that no plaintext lands anywhere on the server.

## Owner decisions (2026-11-10)

- **Q11 auth:** an admin creates client credentials per developer (CLI now, admin
  dashboard in Phase 7). They are exchanged for a short-lived access token plus a refresh
  token. The design is OIDC-ready; SSO (Google Workspace, Entra ID) comes later (ADR-0014).
- **Decryption site:** the **indexer worker** (ADR-0015, a deviation from brief §3.4.3
  for indexing, Proposed). Ciphertext travels through RabbitMQ. The per-session key is
  stored in a separate non-evicting Redis (`keystore`, own `keys` network), wrapped under
  a server key-encryption key (KEK) that only the backend and the indexer hold, so the key
  and the ciphertext never share a store. The worker decrypts in memory, embeds, wipes the
  plaintext (`SecretBytes`) and publishes only vectors and obfuscated metadata. The
  backend writes them to pgvector; the worker has no Postgres access.
- **Q2:** template repos are not ready. A **seeded sample repo** (`server/tests/fixtures/`,
  no client code) stands in for "a real template repo" in the exit criterion.

## In scope (slices; each one is a commit after the owner's yes)

| # | Slice | Scope | Main deliverables |
|---|---|---|---|
| 1 | Decisions and flows | `docs` | ADR-0014, 0015, 0016, 0018, 0019, 0020 (Proposed); sequence diagrams for context awareness and Merkle sync (T53); `docs/privacy.md` with the model-inversion caveat (T51, R6) |
| 2 | Client scaffold and grammar spike | `xype-ai` | `clients/xype-ai` npm workspace package, ESLint `no-restricted-imports` boundaries (rule 03), vitest; a test that loads all 7 web-tree-sitter grammars (Python, TS/TSX, JS, SQL, YAML, Dockerfile, Markdown) under the pinned version; ADR-0017 from the spike's result (R4) |
| 3 | Server security | `security` | `CryptoBox` (AES-256-GCM, directional keys, counter nonces, canonical AAD), `hmac_id` (framed), server-side `SecretScrubber` check and the shared rule file `packages/scrub-rules/`, `KeyStore`, fakes, shared contract tests and cross-language test vectors; `docs/runbooks/key-rotation.md`; proto comments aligned with ADR-0015/0016 |
| 4 | TLS with a local dev CA | `deploy`, `security` | dev CA script and runbook (no keys committed); TLS on the edge proxy, gRPC, asyncpg, Redis and AMQP (plan §3a) |
| 5a | Auth and rate limiting | `gateway` | admin CLI for client credentials; `POST /api/v1/auth/token` and `/refresh` (token families: `refresh_tokens.family_id`, atomic rotation, reuse detection), `GET /api/v1/auth/me`; `CredentialVerifier` seam; per-user (GCRA in Redis) and unauthenticated rate limits (per IP at the edge proxy, per `client_id` failure backoff); revocation publisher on `xype:auth:revoked` |
| 5b | Session keys | `gateway`, `deploy` | the `keystore` Redis and `keys` network, brought forward from slice 8 (TLS-only, own leaf, ACLs); `POST /api/v1/auth/session-key` (X25519, wrapped session key; REST shape gains `server_public_key`, `expires_at`); at most 8 active keys per user (the ninth revokes the oldest); disabling a user and refresh-token reuse delete keys |
| 6 | gRPC `Session` stream | `gateway`, `proto` | `grpc.aio` server, auth on stream open, repository authorization per message, `Hello` (+ `repository_roots`)/`HelloAck`, heartbeat, `TokenRefreshRequired`, revocation closes streams, rate limit, `StreamError`; edge proxy `grpc_pass`; test servers on loopback only (grpcio's native sockets are outside the network guard, recorded in ADR-0012) |
| 7 | Server index store | `indexing`, `storage`, `proto` | wrapped repository HMAC keys in the `/session-key` response (ADR-0016; moved from slice 5); Alembic revision: `repository_keys`, `code_chunks.leaf_node_id`; repository registration with key creation and userinfo stripped from `remote_url` (plan §3a); `VectorStore` (pgvector HNSW) and `MerkleIndex` (DAL), each with a fake and a contract suite; proto: `MerkleRootReport`, `MerkleNode.parent_id`, `IndexAck.rejected`, `ChunkRef.leaf_node_id`; `VectorRecord` keyed by leaf id |
| 8 | Indexer worker | `indexing`, `deploy` | `server/indexer` worker on `xype.index` quorum queues: decrypt, re-check, embed, wipe, publish vectors or rejections; backend consumer writes to the store; the indexer joins `keys` with its `GET`-only `keystore` ACL; import contract and rule 03 / CLAUDE.md wording for the indexer's key-store read; memory re-measured against the ≤ 4 GB budget (R12) |
| 9 | Client privacy core | `xype-ai` | path exclusion and whole-file scrubber from the shared rule file, AES-256-GCM with directional keys and counter nonces, HMAC obfuscation, Merkle tree, credentials in `SecretStorage`; the server's test vectors pass |
| 10 | Client chunking and call graph | `xype-ai` | AST chunking for the 7 languages (line-window fallback per ADR-0017), call graph with dependents |
| 11 | Client sync | `xype-ai` | idle-time worker (> 2 s debounce, CPU cap 10–15%, 10-minute consistency check), gRPC client, initial upload and Merkle delta sync, runs in the Extension Development Host |
| 12 | End to end and report | `evals`, `docs` | index the seeded sample repo end to end; privacy suite end to end; `docs/reports/phase-2.md`; traceability check |

Every slice that adds a message naming a repository adds a cross-team negative test.

Carried forward from slice 3 (review):
- Slice 5b: enforce the 12-hour maximum session-key age from `created_at`.
- Slice 6: the revocation subscriber closes the matching streams (the publisher lands in 5a); the backend's Redis ACL gains `+subscribe` for `xype:auth:revoked`; the edge proxy rate-limits stream opens per IP.
- Slice 8: record a received nonce counter only after `CryptoBox.open` succeeded (the first payload is opened in the indexer).
- Slice 6: the gRPC server serves TLS with the backend's certificate and the edge proxy forwards with `grpc_pass grpcs://` and `grpc_ssl_verify on` (ADR-0036).
- Slice 5b: the `keystore` Redis gets its own leaf (`SERVICES` in `server/devtools/dev_ca.py`; existing CAs need `--rotate-ca`), TLS-only like `redis`, and the key-store client uses `common_lib.tls`; its backend ACL allows `EVALSHA`/`SCRIPT LOAD`.
- Slice 8: the indexer image includes `packages/scrub-rules/rules.json`; the re-check refuses chunks over 64 KiB and runs in `asyncio.to_thread`; `cryptography` goes into the indexer's requirements.

## Privacy rules for every slice

- Each slice that adds a storage or transport path ships its own privacy test (rule 07):
  no plaintext code, file name, symbol or query in Postgres, Redis, RabbitMQ, logs or
  traces.
- Slice 12 adds the end-to-end check: after indexing the sample repo, scan every store,
  log line and span for any substring of the sample repo's code and file names.
- The scrubber runs on the client before encryption. The server repeats the check after
  decryption, and a scrubbed secret never reaches the embedder or a provider adapter.

## Out of scope

Completion, chat and retrieval queries (Phases 3–4); the admin dashboard UI (Phase 7);
SSO providers; JSONB validation for `agent_tasks` and `events` (Phase 5, with their
repositories); hosted TEI; encryption at rest and Docker secrets (before hosting).

## Dependencies to verify live before pinning

`cryptography` (AES-GCM, HKDF); `grpcio` and `grpcio-tools` (the versions ADR-0013 names);
`@grpc/grpc-js` and the TS code generator; `web-tree-sitter` 0.27.1 and the grammar
packages or `.wasm` builds. Each one gets a one-line reason in its slice's summary.

## Acceptance (phase exit)

1. The seeded sample repo is indexed end to end from the extension through the stream,
   the broker and the indexer into pgvector, and a changed file syncs as a delta only.
2. The privacy suite is green: no plaintext in Postgres, Redis, RabbitMQ, logs or traces.
3. Auth: an expired or revoked token cannot open the stream, and refresh rotates.
4. Rate limit: request 201 within a minute is refused with a fixed error.
5. All connections that carry payloads use TLS with the dev CA.
6. Lint, types, tests (coverage ≥ 85% on core modules), size, duplication and import
   checks are green; compose is healthy within the memory budget.
