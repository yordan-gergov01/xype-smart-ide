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
  no real code) stands in for "a real template repo" in the exit criterion.

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

Slice 7 is cut in three (owner, 2026-11-13):
- 7a: the `repository_keys` table (Alembic `0004`); repository registration in the admin CLI (`register-repo`, a `RepositoryAdmin` service the admin dashboard reuses in Phase 7) with key creation and userinfo stripped from `remote_url`; `/session-key` takes `repository_ids` and returns the requested, authorised repository keys wrapped for the client; a shared test vector for the client-side wrap.
- 7b: `code_chunks.leaf_node_id`; the proto additions; `VectorRecord` keyed by leaf id; `VectorStore` (pgvector HNSW) with a fake and a contract suite.
- 7c: `MerkleIndex` (DAL) with a fake and a contract suite (owner, 2026-11-13: 7c is cut in two).
- 7d: the sync engine and its stream wiring: several responses per message, compare `Hello.repository_roots` with the server's roots and start the Merkle sync, `MerkleRootReport`, `MerkleTreeReport` with paging, `MerkleDelta`, a sync deadline, and the server's 10-minute poll (owner, 2026-11-13).

Carried forward from slice 7d:
- Slice 8: the `ChunkUpload` handler settles the held update: when every uploaded chunk is stored or rejected, `MerkleIndex.apply` with the root the sync read, then `IndexAck` (with `rejected`). Risk: an initial index can take hours (R7), while a held update lives only in the stream's memory until the sync deadline; a dropped stream or a slow index starts over from the root. Persisting a settling sync, or applying in parts, is decided with slice 8.
- Slice 11: the client answers `MerkleSyncRequest`s with `last_page` set on the last report, hashes with `inner_hash`'s format, sends a new `MerkleRootReport` after a refused level, and does not answer a poll before its tree is built (an empty tree deletes the stored one).
- Owner decision (ADR-0018 consequences): the index is per repository, so any authorized client defines it; per-branch or per-user roots, or a large-delete guard, before teams share repositories.
- Observability (Phase 7 dashboards): a gauge `xype_merkle_sync_held_nodes` and a counter of `sync too large` refusals, so an exhausted node budget shows on a dashboard, not only in logs.
- Before hosting: cap held sync nodes per user (or one large sync per repository across streams) so one user cannot starve the process budget; size `MERKLE_SYNC_MAX_HELD_NODES` against the container memory (about 0.5 to 1 KB per held node); optionally map store outages in `children`/`root` to the sync refusal like `apply`, and refuse an upsert that re-parents an existing node unless its old parent is in the same update.
- Load tests: whether sync reports should count against the per-user rate limit (an initial index of a large repository sends many), and `apply` time on a large tree.

Carried forward from slice 7c:
- Slice 7d (done): `MerkleTreeReport.last_page`; the sync applies through `MerkleIndex.apply` with the root it read and restarts on `MerkleConflictError`.
- Slice 9: the client's inner hash includes each leaf child's line range (ADR-0018 amendment), with shared vectors.
- Slice 8: a vector delivered after the apply that deleted its leaf must not be stored: the `index.vectors` consumer writes only chunks whose leaf exists in `merkle_nodes`, ordered against `apply` by the repository row lock, with a test (delete the subtree, deliver the late vector, find nothing).
- Slice 7d (done): every level is verified against its parent's hash; a dropped page deletes nothing (tested).
- Slice 7d or the load tests: measure `MerkleIndex.apply` on a large sample tree; its whole-tree reachability check runs under the repository row lock on every sync. If it is too slow, check only the touched nodes (walk up from each stored node's parent). `apply` has a `statement_timeout` and `lock_timeout` (done in 7d). The sync only asks for the children of inner nodes, so a leaf never gets children (done in 7d).
- A migration drops the unused `merkle_nodes.chunk_id` (the link is `leaf_node_id`).

Carried forward from slice 7b:
- Slice 8: the gateway refuses a `ChunkUpload` whose nested `ChunkRef.repository_id` differs from the authorised top-level id (`VectorStore.upsert` refuses it too), with a privacy test; the `index.vectors` consumer writes through `VectorStore`; `VECTOR_EF_SEARCH` joins the settings when the store is wired; `chunk_upload` handling extracts nested `ChunkRef` repository ids (with `leaf_node_id`) and binds `leaf_node_id` in each payload's AAD.
- Slice 7d (done): handle `MerkleRootReport`; `root_hash` and `MerkleNode.parent_id` are validated as HMAC hex at the edge and never echoed.
- Phase 3: retrieval calls `VectorStore.search` only with ids authorised for the principal (Hello or `RepositoryAccess`), never with ids from a request field, with a cross-team test.

Carried forward from slice 7a:
- Before hosting (security): an admin command that re-wraps the repository keys stored under `KEK_PREVIOUS`, and repository-key rotation (retire, drop the index, re-index; update `repositories.hmac_key_id` and the active `repository_keys` row in one transaction), with `docs/runbooks/key-rotation.md`. Until then a KEK rotation is not complete.
- Before hosting (least privilege): the app role may only `SELECT` and `INSERT` on `repository_keys` (or registration runs under a separate admin role).
- A refactor commit: one public DAL transaction helper (`unit_of_work` in `backend/dal/auth/session.py`, the repository store's `_transaction`, `SqlRepositoryAccess`) with a conflict error the auth feature does not own.
- A CLI test that runs `register-repo` end to end and checks that stdout and stderr never repeat a URL.

Slice 6 is cut in two (owner, 2026-11-12):
- 6a: code generation and `Hello.repository_roots`; the `grpc.aio` server with TLS and auth on open; Hello/HelloAck with key and repository checks; heartbeat, `TokenRefreshRequired` and the token-expiry close; rate limit and `StreamError`; edge `grpc_pass` with the per-IP open limit.
- 6b (done): the revocation subscriber (`+subscribe` for `xype:auth:revoked` in the cache ACL) and a per-process stream registry that closes the matching streams; drain on shutdown beyond the grace period.

Carried forward from slice 6a:
- Slices 7, 8, 11: each slice that handles a stream message extracts every repository id it carries (nested `ChunkRef`s included) for `SessionGate.message`, and adds a stream-level cross-team test.
- Slice 6b (done): a stream whose session key was revoked or evicted after Hello stops at once: the revocation subscriber closes it, and a message checks that the key still exists (today it is caught at the first payload's `advance_nonce`, slice 8).
- Slice 11: on `session key unknown, expired or not yours` the extension mints one new key and reconnects once; if that fails again it stops and tells the user (more windows than the 8-key cap). On `CODE_UNAVAILABLE` it reconnects with backoff.
- Slice 8 (first payload): the per-direction use-count limit (2^24) next to the key-age check on every payload.
- Slice 7d (done): compare `Hello.repository_roots` with the server's roots and start the Merkle sync.
- Slice 11: the extension's client sends heartbeats every `heartbeat_interval_s` and reconnects on `TokenRefreshRequired`, a `StreamError` or a close.

Carried forward from slice 5b:
- Slice 6 (done in 6a: `check_key_age` on Hello and every message; the use-count limit moves to slice 8): call `check_key_age` (12 h) and the use-count limit on `Hello` and on each payload; the stream closes with `TokenRefreshRequired` or a fixed `StreamError` when the key is too old.
- Slice 7a (done): `/session-key` takes `repository_ids` and returns the wrapped repository keys (additive: the request model forbids unknown fields today).
- Slice 8: the indexer's `keystore` ACL user (`-@all +get %R~xype:sk:*`), the indexer joins `keys`, and the compose test's `keys` members become `{keystore, backend, indexer}`; re-measure `keystore` memory.

Carried forward from slice 3 (review):
- Slice 5b (done: `check_key_age` and `expires_at`; the calls move to slice 6, above): enforce the 12-hour maximum session-key age from `created_at`.
- Slice 6 (per-IP open limit done in 6a; the rest is 6b): the revocation subscriber closes the matching streams (the publisher lands in 5a); the backend's Redis ACL gains `+subscribe` for `xype:auth:revoked`; the edge proxy rate-limits stream opens per IP.
- Slice 8: record a received nonce counter only after `CryptoBox.open` succeeded (the first payload is opened in the indexer).
- Slice 6 (done in 6a): the gRPC server serves TLS with the backend's certificate and the edge proxy forwards with `grpc_pass grpcs://` and `grpc_ssl_verify on` (ADR-0036).
- Slice 5b (done): the `keystore` Redis gets its own leaf (`SERVICES` in `server/devtools/dev_ca.py`; existing CAs need `--rotate-ca`), TLS-only like `redis`, and the key-store client uses `common_lib.tls`; its backend ACL allows `EVALSHA`/`SCRIPT LOAD`.
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
