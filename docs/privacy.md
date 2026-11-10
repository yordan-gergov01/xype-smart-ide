# Privacy model

What the server may hold, what it never holds, and the limits of these guarantees. Brief §3.7, rule 07. Decisions: ADR-0011 (metadata-only logs), ADR-0014 (credentials), ADR-0015 (keys and decryption), ADR-0016 (obfuscation), ADR-0018 (Merkle sync), ADR-0019 (scrubbing), ADR-0020 (vector store).

## Where data lives

| Store | Holds | Never holds |
|---|---|---|
| Client memory | Plaintext code, file names, the session key, repository HMAC keys | — |
| Client disk | `client_secret` and refresh token in VS Code `SecretStorage` (OS keychain); the encrypted short-term completion cache (Phase 3) | Session or repository keys |
| RabbitMQ | Ciphertext, `ChunkRef`s and vectors, in memory and in quorum-queue segments on disk | Keys in any form, plaintext |
| `keystore` Redis | Session keys **wrapped** under the KEK, nonce counters, per-user key lists | Ciphertext, plaintext, tokens |
| Shared Redis | Rate-limit counters by user id, revocation messages | Ciphertext, plaintext, keys, tokens |
| Postgres | Vectors, HMAC ids, line ranges, chunk kinds, language ids, Merkle hashes; repository keys wrapped under the KEK; refresh tokens and client secrets as HMACs; organisation metadata in clear: `repositories.name`, `remote_url` (without userinfo), `projects.name`, user emails | Plaintext code, file names inside a repository, symbols, queries, answers, unwrapped keys |
| Logs, traces, metrics | Ids, sizes, latencies, counts, fixed error codes | Bodies, code, names, queries, answers, tokens |
| Process memory (indexer, gateway) | Plaintext in `SecretBytes`, only while embedding or building a prompt | — |

Reading code back needs three things: ciphertext from RabbitMQ, its wrapped key from the key store, and the KEK. No single store holds all three. The KEK and the store credentials, however, sit together in the backend's and indexer's configuration (see the limits below).

## Model-inversion caveat

Embedding-only storage is **not** a cryptographic guarantee. Research on embedding inversion shows that text can be partly reconstructed from its embedding vector, especially short or common text. Someone with read access to `code_chunks`, or to vectors in RabbitMQ's segments on disk, and with the same embedding model could recover fragments of code. Mitigations:
- **Scoped access.** Only the backend's runtime role reads `code_chunks`, and workers have no Postgres access (ADR-0035, ADR-0034).
- **Key rotation.** Repository HMAC keys rotate on compromise (ADR-0016). Session keys rotate every 12 hours at most (ADR-0015). KEK rotation re-wraps the stored keys.
- **Scrubbing.** Secrets that the rules detect are removed before embedding (ADR-0019). Secrets the rules miss can be embedded, and inversion could partly recover them.
- **Encryption at rest.** Postgres and RabbitMQ volumes are encrypted before hosting (plan §3a).

## What the server can infer

- **Tree shape.** How many directories, files and chunks there are and how they nest, but not their names (ADR-0018).
- **Sizes.** Each chunk's size, from the ciphertext length, and its line range.
- **Fingerprints.** Tree shape, line ranges and sizes together can identify well-known code, such as vendored open-source files or XYPE templates.
- **Equality.** Two chunks with the same scrubbed content in one repository have the same hash (ADR-0016).
- **Activity.** When and how much each developer changes, from sync timing and sizes.
- **Missed secrets.** Whether a chunk held a secret that the client did not scrub, from the counter per rule (ADR-0019).

## Honest limits

- **Configuration.** The backend's or indexer's configuration (the KEK plus the key-store and broker credentials) is enough to decrypt everything queued for up to 24 hours. Locally the KEK sits in the compose environment file on the host. Docker secrets and a KMS are needed before hosting (plan §3a).
- **Running processes.** An attacker who controls the indexer or gateway while it runs can read the plaintext it handles. The guarantees are about storage and transport, not about a compromised running process.
- **Memory wiping** is best effort in Python. The crypto library and the HTTP request to the embedder make copies that cannot be zeroed (`server/common_lib/secret_bytes.py`).
- **Repository keys.** The server holds them in wrapped form. With the database and the KEK, likely paths could be guessed by trial. Every developer of a team holds them in memory while working.
- **Key exchange.** The X25519 layer keeps session keys out of passive records on the path. It does not stop an active party that can impersonate the server's TLS endpoint (ADR-0015).
- **Model providers.** From Phase 3 on, the model receives scrubbed plaintext prompts. That is the local Ollama during development, and a paid third party only after Phase 10 approval.
- **Rejected chunks.** A chunk the server re-check rejected is stored as a Merkle leaf with its content HMAC. With the database and the KEK, guesses of the low-entropy secret it holds could be confirmed offline.
- **Database role.** The backend's runtime role can read the wrapped repository keys; there is no separate key-service role yet.
- **Selective fetch.** A compromised server could ask for any chunk; the client serves only refs in the active request's scope, within count and byte caps (ADR-0013, Phase 4).
- **PII coverage.** Scrubbing covers emails and phone numbers only. IP addresses, personal names and postal addresses are not detected (ADR-0019).

## How it is checked

The privacy suite lives in `server/tests/`. It grows from Phase 2 slice 3 on, with one test per new storage or transport path, and slice 12 adds the end-to-end run.

The end-to-end run indexes the seeded sample repo, then scans Postgres, both Redis instances, RabbitMQ messages, log output and spans for:
- any substring of the sample's code;
- the sample's file names;
- unkeyed hashes of its paths.

A planted secret must never reach the embedder fake or the mock provider, and a denied path must never produce a `ChunkRef`.
