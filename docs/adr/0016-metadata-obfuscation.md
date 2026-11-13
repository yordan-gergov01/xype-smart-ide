# 0016 — Metadata obfuscation: keyed HMAC-SHA256 per repository for file ids, symbols and chunk hashes

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §3.3 (obfuscated metadata), §3.4.2, §3.7

## Context and problem statement

The server needs stable identifiers for files, symbols (call-graph edges), Merkle nodes and chunk contents, so it can index, diff and ask the client for exact chunks. It must never learn the file names, symbols or content behind them. Every developer on a team who opens the same repository must produce the same identifiers, otherwise the index splits per person. The schema already holds 64-hex HMAC columns and `repositories.hmac_key_id` (ADR-0035).

## Decision drivers

- Deterministic within a repository, across developers and sessions.
- Not reversible by dictionary: common paths such as `src/main.py` must not be guessable from an unkeyed hash.
- Separation per repository, so the same file in two repositories gives unrelated ids.
- Keys must survive restarts: losing one forces a full re-index.
- Simple: one function, no external dependency (`docs/interfaces.md`).

## Considered options

1. **HMAC-SHA256 under a random 256-bit key per repository, stored durably and issued by the server to authorised team members.**
2. Plain SHA-256 of the path.
3. A key that each developer holds locally and shares out of band.

## Decision outcome

Chosen option: **1**.

- **Function.** `hmac_id(repo_key, domain, *fields) = hex(HMAC-SHA256(repo_key, frame(domain) ‖ frame(field_1) ‖ …))`, where `frame(x)` is the 4-byte big-endian length of the UTF-8 bytes of `x`, followed by those bytes. The domains are `file`, `dir`, `symbol`, `chunk-pos` and `chunk-content`, which keeps identifiers of different kinds from colliding. Framing makes every field boundary unambiguous. Python and TypeScript share one specification and one set of test vectors.
- **Normalisation before hashing.**
  - Paths are relative to the repository root, use `/` separators and NFC Unicode, keep their case, and have no leading `./`.
  - A symbol is `language:qualified.name`.
  - Chunk content is the scrubbed text (ADR-0019) with LF line endings.
- **Keys.**
  - The server creates a random key when a repository is registered (slice 7).
  - It is stored durably in Postgres, in a new table `repository_keys (repository_id, hmac_key_id, wrapped_key, kek_id, created_at, retired_at)`. The key is wrapped under `KEK_repo = HKDF(KEK, "repo-keys")` (ADR-0015) with AAD `"repo-hmac" ‖ repository_id ‖ hmac_key_id ‖ kek_id`. This is a schema change through Alembic in slice 7 (ADR-0035).
  - The indexer cannot read the table: it has no Postgres access.
  - During the session-key exchange (ADR-0015) the backend unwraps the keys of the **requested** repositories that the caller's team may access, holding them in `SecretBytes`. It re-wraps them for the client and wipes them. The server never computes identifiers with a repository key.
- **Rotation.** A new key means new identifiers, so the server drops the repository's index and the client re-indexes. Rotation is an admin action (key compromise, or a developer leaving with a copy), not a schedule. The old row gets `retired_at` and is deleted once the re-index finishes.

### Consequences

- Good: identifiers are stable per repository and meaningless without the key. The server stores and compares them blindly.
- Bad: equal content gives equal chunk hashes within a repository, so the server can see that two chunks are identical, though not what they contain. The Merkle diff needs this. Rows are keyed by the leaf's position id, not by content hash, so identical chunks never collide (ADR-0020).
- Bad: the server holds repository keys in wrapped form, readable by the backend's runtime role. An attacker with the database and the KEK could brute-force likely paths. A table grant limited to a separate key-service role is a hosting-time option; scoped access and KEK handling are the mitigation (`docs/privacy.md`).
- Bad: every developer of a team receives the key, so a leaving developer keeps a copy until it is rotated.
- Bad: rotation costs a full re-index.

### Confirmation

- Shared test vectors for `hmac_id` across Python and TypeScript (slices 3 and 9). They include fields that would collide without framing.
- A test that the same path under two repository keys gives different ids.
- A test that the session-key exchange returns keys only for requested, authorised repositories.
- The privacy suite checks that no stored identifier equals an unkeyed SHA-256 of any sample-repo path.

### Implementation notes (slice 7a)

- **Table.** `repository_keys (repository_id, hmac_key_id, kek_id, wrapped_key_enc, created_at, retired_at)`, primary key `(repository_id, hmac_key_id)`, Alembic `0004`. `wrapped_key_enc` is `nonce(12) ‖ ciphertext ‖ tag(16)`: a CHECK holds it at 60 bytes, and the `_enc` suffix is the rule for every BYTEA column. A partial unique index allows one active key (`retired_at IS NULL`) per repository.
- **Registration.** `RepositoryAdmin.register` (admin CLI `register-repo` now, the admin dashboard in Phase 7) makes the repository id and `hmac_key_id` (UUIDv7) first, wraps a fresh key under `KEK_repo` with AAD `frame("repo-hmac", repository_id, hmac_key_id, kek_id)`, wipes it, and writes both rows in one transaction. Only `https`, `ssh` and `git` URLs, rebuilt from the host and a numeric port (so any userinfo, query and fragment is dropped), and scp-like `[git@]host:path` are stored; any other scp user name is dropped, since it may be a token. Local paths, other schemes, non-ASCII and anything with `@` left after the rebuild are refused (`without_credentials`). `ssh://git@host` is stored without `git@`: the URL is metadata, not a clone address. The same patterns are a database CHECK (`common_lib.remote_urls`). Errors never repeat the URL.
- **Exchange.** `/session-key` takes up to 64 `repository_ids`. One query on the primary returns the active keys of those that belong to the caller's team, so another team's id and an unknown one are left out the same way, and replica lag right after registration cannot look like "not yours". The keys are read before the session key is stored, so a failed read leaves no orphan key. Each is unwrapped, sealed for the client with AAD `frame("repo-hmac", repository_id, hmac_key_id)` (`seal_repository_key`, a shared vector) and wiped. A row that fails authentication fails the whole exchange.
- **Not built yet.** Re-wrapping repository keys after a KEK rotation, and rotating a repository key. Until the re-wrap exists, `KEK_PREVIOUS` stays set after a KEK rotation (`docs/runbooks/key-rotation.md`).

## Pros and cons of the options

### 2. Plain SHA-256

- Good: no key management.
- Bad: reversible by dictionary for common paths and symbols, which defeats the purpose.

### 3. A key held per developer

- Good: the server never sees the key.
- Bad: each developer's index is separate, or the key must be passed around by hand. Both fail for a team.

## More information

- ADR-0015 (KEK, subkeys and key exchange), ADR-0018 (Merkle node ids), ADR-0035 (the new table), `server/common_lib/models/index.py`.
- The proto comments for `obfuscated_file_id`, `MerkleNode.node_id` and `snippet_hash` predate this ADR and are corrected in slice 3. `snippet_hash` (the completion cache) belongs to ADR-0021.
