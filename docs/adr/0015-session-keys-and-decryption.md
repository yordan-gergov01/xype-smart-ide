# 0015 — Client-side crypto: AES-256-GCM per-session keys, X25519 key exchange, KEK-wrapped keys, decryption in the indexer

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §3.4.2, §3.4.3, §3.4.7, §3.7

## Context and problem statement

Code leaves the client only as AES-256-GCM ciphertext (`EncryptedPayload`, ADR-0013). The server must decrypt it in memory to embed it, and later (Phases 4–5) to build prompts. Three questions need an answer: how a key reaches both sides, where the key lives while queued work waits, and which process decrypts.

Constraints:
- Rule 07: no plaintext code in Postgres, Redis, RabbitMQ, disk, logs or traces. A key stored next to its ciphertext counts as plaintext.
- Workers have no database access (ADR-0006).
- The first index of a large workspace takes hours on CPU (R7), so it must be queued and resumable.

The owner chose decryption in the indexer worker (2026-11-10). This **deviates from brief §3.4.3** ("in-memory decryption" at the gateway) for the indexing path only: the gateway still decrypts in memory for completion and chat (Phases 3–4), while chunks being indexed are decrypted by the embedding engine (brief §3.4.7). The ADR stays Proposed until the owner approves the deviation.

## Decision drivers

- No single store holds both a usable key and the ciphertext it opens.
- Embedding runs off the gateway, asynchronously, resumable after a crash.
- Keys are short-lived, scoped to one user's session and to the repositories that user's team may access; rotation is routine and enforced by the server.
- The design works the same locally and hosted. KMS can replace the environment KEK later.

## Considered options

1. **The indexer worker decrypts.** Ciphertext travels through RabbitMQ; the session key is stored in Redis, wrapped under a key-encryption key (KEK) that only the backend and the indexer hold.
2. The gateway decrypts and calls the embedder in-process, with no broker hop.
3. The backend sends the key with the job, wrapped under the KEK, inside the same broker message.

## Decision outcome

Chosen option: **1**.

### Authorization

The gateway authorizes every `repository_id` in every message (`Hello`, Merkle sync, `ChunkUpload`, searches) against the principal's team, and refuses the message with `StreamError.CODE_INVALID_REQUEST` otherwise. The gateway, not the client, writes `user_id`, `team_id` and `repository_id` into each queue message. Each slice that adds a message adds a cross-team negative test.

### Key exchange

`POST /api/v1/auth/session-key` is authenticated, runs over TLS, and has its own limit of 10 calls per minute per user. A user holds at most 8 active session keys; minting a ninth revokes the oldest.

1. The client generates an ephemeral X25519 key pair and sends its public key with the ids of the repositories it wants to use.
2. The server generates a random 256-bit session key and a `key_id` (UUIDv7), bound to the caller's user id and token family (`fam`, ADR-0014).
3. It computes the X25519 shared secret and refuses an all-zero result. It derives the wrapping key as `HKDF-SHA256(shared, salt = key_id, info = "xype session-key v1" ‖ client_pub ‖ server_pub ‖ user_id)`.
4. It returns `{key_id, server_public_key, wrapped_key, expires_at}`, where `wrapped_key` is AES-256-GCM of the session key under the wrapping key with AAD `"session" ‖ key_id`. The same response carries the HMAC keys of the requested repositories that the team may access, each wrapped with AAD `"repo-hmac" ‖ repository_id ‖ hmac_key_id` (ADR-0016). The REST shape in `session.proto` gains these fields in slice 5.
5. The client unwraps the keys and keeps them in memory only, never on disk. It names the key in `Hello.session_key_id`.

This layer keeps the session key out of passive records on the path, such as edge-proxy request logs and captured traffic after TLS termination. It does **not** protect against an active party that terminates TLS and swaps the server's public key. TLS with the CA the client trusts is the protection there. Signing `server_public_key` with a long-term server key pinned in the client is a hosting-time option.

### Directional keys and nonces

- Both sides derive `k_c2s = HKDF(session key, "c2s")` and `k_s2c = HKDF(session key, "s2c")`. Each direction encrypts only with its own key, so a server-to-client message cannot be reflected back as client-to-server.
- The nonce is 4 zero bytes followed by a 64-bit counter, per direction and per key. The receiver keeps the highest counter seen per `(key_id, direction)`: the server in the key store, the client in memory. It refuses any counter that is not higher. One stream is ordered, so this replaces an unbounded set of seen nonces.
- **One key, one stream.** A key is bound to the first stream whose `Hello` names it (its `session_id`) and is refused on any other stream, so two windows or two gateway replicas can never encrypt under the same key. After a reconnect the client mints a new key.
- The server allocates its `k_s2c` counters with an atomic `INCR` on `xype:skc:<key_id>` in the key store before each encryption, never from process memory, so a replica restart cannot reuse a nonce.
- The server enforces a maximum key age of 12 hours and at most 2^24 encryptions per direction, and refuses payloads under a key past either limit. The client asks for a new key before then, or on reconnect.

### Encodings

Every concatenation `a ‖ b ‖ …` in this ADR (HKDF `info`, wrap AADs, payload AAD) uses ADR-0016's `frame()`: each field is preceded by its 4-byte big-endian length, so the shared test vectors are unambiguous.

### AAD

The AAD is the framed encoding of these fields:

`version ‖ direction ‖ key_id ‖ message_id ‖ request_id ‖ field_number ‖ repository_id ‖ obfuscated_file_id ‖ chunk_hash ‖ line_start ‖ line_end`

Fields that a message has no use for are empty. Binding the `ChunkRef` stops a ciphertext from being moved to another chunk or repository. The receiver rebuilds the AAD from the plain fields and never trusts the `aad` bytes it was sent. The GCM tag, and therefore the AAD, can only be checked with the key, so the gateway checks the plain envelope fields and the indexer verifies the tag when it decrypts.

### Server-side key storage

- **A separate key store.** The shared Redis evicts (`allkeys-lru`, 64 MiB) and sits on the `data` network with Postgres (ADR-0034), so it cannot hold key material, and workers may not join `data`. Session keys therefore live in a second Redis instance, `keystore`:
  - `maxmemory-policy noeviction`, no persistence, about 32 MiB;
  - on its own internal network `keys`, which only `backend` and `indexer` join;
  - a restart is a key rotation: clients mint a new key, queued chunks fail to decrypt, go to the DLQ and are re-uploaded on the next sync.
- Key `xype:sk:<key_id>` holds `AES-256-GCM(KEK_session, session_key, aad = key_id ‖ user_id ‖ kek_id)` plus `kek_id`. `KEK_session = HKDF(KEK, "session-keys")`, so session keys and repository keys (ADR-0016, in Postgres) are wrapped under different subkeys.
- Key `xype:skn:<key_id>:<direction>` holds the highest nonce counter seen: one integer per direction, not a set.
- Sets `xype:sku:<user_id>` and `xype:skf:<family_id>` list a user's and a token family's key ids. Disabling a user deletes all of the user's keys; refresh-token reuse (ADR-0014) deletes the keys of that family only. Queued work under a deleted key stops decrypting.
- The TTL is 24 hours (`SESSION_KEY_TTL_S`): a working day plus queued jobs that outlive the stream.
- The KEK is 256 bits. It comes from the environment (`KEK_CURRENT`, `KEK_PREVIOUS`, each with an id), moves to Docker secrets before hosting and to a KMS later (plan §3a). Compose gives it to `backend` and `indexer` only. Rotation re-wraps the stored keys, and `KEK_PREVIOUS` is removed once nothing is wrapped under it. The procedure goes into `docs/runbooks/key-rotation.md` in slice 3.
- ACLs on `keystore`: the backend's user may read and write `xype:sk*`, with no channels. The indexer's user is `-@all +get %R~xype:sk:*`: one command, one key pattern, no `SCAN` or `KEYS`.

### Indexing flow

Sequence diagram: `docs/flows/context-awareness.md`.

1. The gateway checks a `ChunkUpload`'s plain fields: principal, repository authorization, `key_id` ownership, key age and use count, nonce counter.
2. It publishes the ciphertext, the `ChunkRef`s and the AAD inputs (message id, request id, field number, direction) to the `index.chunks` quorum queue on a new exchange `xype.index`, with a message TTL no longer than the key's remaining TTL. This extends ADR-0004: `index.chunks` and `index.vectors`, each with a `.dlq` partner on `xype.index.dlx`.
3. The indexer fetches and unwraps `xype:sk:<key_id>`. It rebuilds the AAD, decrypts into `SecretBytes`, runs the server-side scrubber check (ADR-0019), embeds, and wipes the plaintext and the unwrapped key.
4. It publishes only vectors, `ChunkRef`s, the repository id and the model id and version to `index.vectors`.
5. A backend consumer writes them through `VectorStore` (ADR-0020) and updates the Merkle tree (ADR-0018).

A chunk that fails the server scrub re-check is not embedded. It is reported back as rejected and counts as settled for the Merkle sync (ADR-0018, ADR-0019). A chunk whose key has expired, or whose tag fails to verify, goes to the dead-letter queue with a fixed reason. Dead-lettered messages hold ciphertext only and expire with their key (at most 24 hours); after that they could not be decrypted anyway. The client re-uploads the chunk on the next Merkle sync.

### Consequences

- Good: RabbitMQ holds ciphertext without its key, and Redis holds keys without ciphertext. Reading code needs both stores plus the KEK.
- Good: embedding scales and fails independently of the gateway; the first index resumes from the queue.
- Bad: the backend's or indexer's configuration (KEK, Redis and AMQP credentials) is enough to decrypt everything queued for up to 24 hours. Protecting that configuration (Docker secrets, KMS) is part of hosting (`docs/privacy.md`).
- Bad: the indexer needs read access to the key store (`GET xype:sk:*` only). This widens the "workers have no store access" rule (ADR-0006, rule 03) for this one worker and this one store. It still has no Postgres access and does not join `data`.
- Bad: one more container (`keystore`, about 32 MiB) and one more network, within the ≤ 4 GB budget (re-measured in slice 8).
- Bad: more moving parts (`index.chunks` → indexer → `index.vectors` → backend consumer), and queued work must finish within the key TTL.
- Bad: wiping is best effort in Python (`common_lib/secret_bytes.py`). The crypto library and the model-server request create copies that cannot be zeroed.
- Bad: the server needs a crypto library for AES-GCM, X25519 and HKDF, chosen in slice 3. The client uses Node's built-in `crypto` and adds none.

### Confirmation

- The Python and TypeScript implementations check shared test vectors for the key derivation, the directional keys, the AAD encoding and AES-GCM (slices 3 and 9).
- Contract tests for `CryptoBox` and `KeyStore` with their fakes. They refuse a second stream under one key, and they refuse: a wrong AAD, a moved ciphertext (other `ChunkRef`), a reflected message, a foreign `key_id`, a repeated or lower nonce counter, an expired or over-used key, and an all-zero shared secret.
- Cross-team negative tests for every message that names a repository.
- After indexing, a privacy test scans RabbitMQ messages, Redis values and logs (slice 8): no plaintext and no unwrapped key.
- ADR-0034's compose test asserts that only `backend` and `indexer` receive the KEK and join `keys`, that the indexer does not join `data`, and that `keystore` uses `noeviction`. A test checks the indexer's ACL.

## Pros and cons of the options

### 2. The gateway decrypts in-process

- Good: no key in Redis, fewest places that hold a key.
- Bad: the gateway carries the embedding CPU load, and a 4-hour first index has no resumable queue.
- Bad: the backend must join the `models` network.

### 3. The key wrapped inside the same broker message

- Good: no Redis lookup in the worker.
- Bad: anyone who reads the broker and holds the KEK can read every queued chunk, so the broker becomes a single point of exposure. This breaks the "key next to ciphertext" rule in spirit.

## More information

- Amends ADR-0013 (nonce and AAD rules), ADR-0004 (the `xype.index` exchange and queues), ADR-0034 (the `keystore` service and `keys` network) and ADR-0006 (the indexer's key-store read).
- Related: ADR-0014 (revocation), ADR-0016 (repository keys), `docs/privacy.md` (limits and the model-inversion caveat).
- Chat persistence (`chat_messages.content_enc`, Phase 4) needs a long-lived key and is decided then, not here.
