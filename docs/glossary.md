# Glossary

Domain language for XYPE IDE. Use these terms in code, specs, tickets and commits. Definitions say what a thing **is**, not how it is built. Maintained with the `domain-modeling` skill.

## Product

**XYPE IDE**: the AI-native editor: a Code-OSS fork plus the `xype-ai` extension and its server.
_Avoid_: the app, the tool

**Workspace**: the set of folders a developer has open in one IDE window.

**Repository**: a registered git repository the AI may index and work on.
_Avoid_: project (means something else here), codebase (when one repo is meant)

**Template**: a registered repository that holds a framework pattern (FastAPI, React+TS, deploy, …) used as reference and as the source of new projects.
_Avoid_: boilerplate, starter

**Project**: a unit of work that owns repositories and budgets; optionally linked to the project-context system by `projects.external_ref`.

**Project-context system**: the external project-management system that holds project and team context; separate from Jira.

**Project-context connector**: the `ProjectContextClient` interface (a `ConnectorClient`) and its adapters, which read project and team context from the project-context system.

**Team**: a group of developers sharing caches, repositories and budgets.

**Rules pack**: a set of conventions injected as the stable prefix of every prompt. There are two: the **shared rules pack**, common to all registered repositories, and the **repo rules pack** (`.xype/rules.md`).
_Avoid_: shared rules (means the scrub rules in `packages/scrub-rules/`)

## Context engine

**Chunk**: an AST-delimited piece of code (function, class, block) and the unit of indexing and retrieval.

**Re-request**: a sync that ends asks the client again for its repository's stale pending leaves, those neither asked for nor published within the settle window, a bounded number of times (ADR-0018, slice 8c3c).

**Settle window**: how long a pending leaf may wait for the indexer's result after it was asked for or published before it is asked for again (`INDEX_SETTLE_WINDOW_S`, 900 s).

**Chunk upload**: a `ChunkUpload` the gateway accepts: only chunks of pending leaves with the same hash and file go to the indexer, each after its nonce counter advanced; `IndexAck` then means accepted, not indexed (ADR-0015, slice 8c3b).
_Avoid_: snippet (reserved for the code around the cursor), fragment

**Obfuscated id**: a keyed hash that stands in for a file path or symbol name outside the client.

**Merkle tree**: the hash tree over a repository's files and chunks used to find what changed.

**Root report**: the client's `MerkleRootReport` after a debounced change; a different root starts a sync (ADR-0018).

**Rejected chunk**: an uploaded chunk the server did not embed, reported in `IndexAck.rejected` with a fixed rejection reason; it counts as settled for the Merkle sync (ADR-0018, ADR-0019).

**Merkle index**: the server's stored copy of a repository's Merkle tree (`merkle_nodes` and the root in `repositories`), changed only by a whole sync's `apply` with compare-and-set on the root.

**Dead letter**: a queue message no handler could process, copied to its queue's `.dlq` with a fixed reason (`x-xype-reason`) and kept at most 24 hours (ADR-0015).

**Retry queue**: a delay queue (`index.<q>.retry.<n>`) where a failed message waits about 1, 2 or 4 minutes before it returns to its queue (ADR-0004).

**Leaf node id**: the HMAC of a chunk's position (`chunk-pos`, ADR-0018), the Merkle leaf's id. It keys the stored chunk (`code_chunks.leaf_node_id`), so identical content in two places stays two rows, and it is part of the payload AAD.

**Delta sync**: re-indexing only the chunks whose Merkle hashes differ between client and server.

**Selective fetch**: the server asking the client, over the open session stream, for the exact chunks it needs.

**Scrubbing**: replacing secrets and personal data with `<SECRET_REMOVED>` on the client before encryption; the server repeats the check after decryption (ADR-0019).

**Path exclusion**: the client's check before a file is read: a denied path (`denied_paths` in the shared rule file), a file over 1 MiB, a binary file or one that is not UTF-8 never produces a chunk (ADR-0019).

**Scrub corpus**: the shared, synthetic test cases (`packages/scrub-rules/corpus.json`) that both scrubber implementations must reproduce exactly.

**Pending leaf**: a Merkle leaf the server stored whose chunk has no result yet; the indexer's result settles it as `indexed` or `rejected` (ADR-0018, slice 8c2).

**Settle**: to apply the indexer's result to its pending leaf, in one transaction ordered against `apply`; a result for a leaf that is no longer pending with its hash is `not_pending`, and a vector of another embedding model is `wrong_model` and leaves the leaf pending (ADR-0018).

**Scan process**: a child process of the indexer that runs the server's re-check on one chunk at a time, and is killed when a scan runs past its time (`indexer.scan`, ADR-0019).

**ReDoS check**: the test that runs each scrub rule alone on adversarial inputs (`packages/scrub-rules/adversarial.json`) under a time bound, so no rule backtracks badly (ADR-0019).

**Session stream**: the persistent bidirectional gRPC connection between one IDE window and the server. It lives at most as long as its access token and closes with a `StreamError` (ADR-0013).

**Session gate**: the session service's checks on a stream: the token when it opens, the session key and repositories at Hello, and the rate limit and repositories on every later message (ADR-0013).

**Hello**: the first message on a session stream; it names the session key and the repositories the window works on.

**Stream registry**: a backend process's open session streams by user and token family, so a revocation or a shutdown reaches them at once (ADR-0013).

**Drain**: on shutdown, telling every open stream to reconnect (`CODE_UNAVAILABLE`) before the server stops.

**Session key**: the AES-256 key, issued per session and user, that encrypts code between the client and the server (ADR-0015).

**Repository key**: the per-repository HMAC key behind every obfuscated id of that repository (ADR-0016). Created at registration, stored in `repository_keys` wrapped under the KEK, and handed to the team's developers with their session key.

**Registration**: an admin adds a repository to a team (`register-repo`); it creates the repository key and strips credentials from the remote URL.

**KEK**: the server's key-encryption key; session and repository keys are stored only wrapped under it.
_Avoid_: master key

**KEK ring**: the current KEK, which wraps, and the previous one, which still unwraps during a rotation (ADR-0015).

**Direction key**: the key derived from a session key for one direction of the stream (client-to-server or server-to-client).

**Counter nonce**: a GCM nonce of four zero bytes and a 64-bit counter; the receiver refuses any counter not above the highest it has seen.

**Framing**: the length-prefixed encoding (`frame`) used for every keyed hash, key derivation and AAD, so field boundaries are unambiguous.

**ID domain**: the kind of thing an obfuscated id names (file, dir, symbol, chunk position, chunk content); part of the HMAC input.

**Client credentials**: the `client_id` and secret an admin issues to one developer, exchanged for tokens (ADR-0014).

**Principal**: the authenticated caller of a request or stream: user, team and role.

**Token family**: the chain of refresh tokens of one IDE window; reusing a revoked one revokes the whole family.

**Wrapping key**: a key used only to encrypt another key, such as the KEK or the key derived during the session-key exchange.

**Key store**: the non-evicting Redis instance that holds wrapped session keys and nonce counters (ADR-0015).

**Revocation tombstone**: a short-lived key-store entry, written when a user or token family is revoked, that refuses new session keys while the revoked access tokens still verify (ADR-0015).

**Session-key cap**: the most session keys a user holds at once (8); minting one more deletes the oldest (ADR-0015).

## AI features

**Completion**: an inline code suggestion at the cursor, including fill-in-the-middle; the synchronous path.

**Chat**: a question answered with codebase context, scoped to files or the whole codebase; streamed.

**Agent task**: a long-running background job (tests, refactor, docs, implement a ticket) that runs tools on the client.

**Client tool**: an action an agent task asks the client to perform (read, search, propose edit, run tests/commands), subject to approval rules.

**Backup model**: a faster, cheaper model used when the primary is slow, failing or over budget; its answers are labelled as such.

**Weight-space programming**: improving a model by having another model read and edit its weights directly, without gradients; the only fine-tuning method of Phase 9 (ADR-0037).

**Forward-pass budget**: the fixed number of times the editing model may run the target model on the practice split during one weight-space session.

**Practice split**: the samples a weight-space editing session may see; held out by repository from the eval split, which only the harness sees.

**Off-peak window**: the hours when a provider's prices are lower; non-urgent work may be deferred into them.

## Cost

**Budget**: the monthly USD limit per user or project for paid model usage.

**Hard cap**: the monthly limit above which every paid call is blocked.

**Cost ledger**: the record of every model call's tokens and USD cost.

## Code structure

**Feature**: one brief module (retrieval, cache, router, …) as it appears in the server: a package per layer under `backend/api`, `backend/services` and `backend/dal` (ADR-0006).
_Avoid_: package (means a Python distribution here)

**Layer**: one of `api`/`grpc` (transport), `services` (business rules) and `dal` (data access); imports point only downwards.

**Correlation id**: the id that follows one request across REST, gRPC, RabbitMQ and workers (`x-correlation-id`); it appears in every log line and span of that request.

**Readiness**: whether a service can reach every dependency it needs right now (`/readyz`); liveness (`/healthz`) only says the process runs.

**Schema owner**: the database role that owns the schema; only the one-shot migrate service uses it.

**Runtime role**: the database role the backend connects with; it reads and writes rows but cannot change the schema.

**Embedder**: turns text into a vector; documents (code chunks) are embedded as they are, queries with an instruction.

**Instruction**: the retrieval task put in front of a query before it is embedded (`Instruct: …\nQuery:…`); a versioned prompt.

**Clock**: the seam that tells services the current time, so tests control it.

**Contract suite**: one set of tests that a fake and every real adapter of an interface must all pass (`server/tests/support/contracts`).

**Mock provider**: the deterministic, free `LLMProvider` used in development, tests, CI and evals; it never calls a model.

**Grammar**: the WebAssembly parser for one language that web-tree-sitter loads to chunk code by its syntax tree (ADR-0017).

**Network guard**: the test-session guard that refuses every connection from the test process to a host other than this machine (ADR-0012).

**SecretBytes**: a buffer of plaintext code that never prints its content and is wiped right after use.

**Edge proxy**: the only container with a published API port; it forwards to the backend, which has no route outside the stack.

**Principal**: who is calling, as the gateway authorizes it: user, team and role.

**Client credentials**: the `client_id` and secret an admin creates for one developer; the secret is shown once and stored only as a peppered HMAC (ADR-0014).

**Token family**: the chain of refresh tokens that starts with one login (one IDE window). Each refresh revokes the presented token and issues the next; presenting a revoked one revokes the whole family.
_Avoid_: session (that is the gRPC stream)

**Pepper**: the server-side key under which client secrets and refresh tokens are HMACed before they are stored, so a database dump cannot be checked offline.

**Dev CA**: the local certificate authority in `deploy/certs/` (never committed) that issues one server certificate per TLS service of the stack; name constraints limit it to the stack's names (ADR-0036).
_Avoid_: self-signed certificate (each leaf is signed by the CA)
