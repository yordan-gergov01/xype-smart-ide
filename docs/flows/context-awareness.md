# Flow: context awareness (initial index and selective fetch)

Brief §3.5. Decisions: ADR-0013, ADR-0014, ADR-0015, ADR-0016, ADR-0019, ADR-0020. Implemented in Phase 2 (indexing); the query part follows in Phase 4.

Every arrow that carries code, paths or queries carries an `EncryptedPayload`. Everything else is an id, an HMAC, a line range, a size or an enum. The gateway authorizes every `repository_id` against the caller's team on every message.

## Session setup

```mermaid
sequenceDiagram
    autonumber
    participant C as xype-ai (client)
    participant G as Gateway (backend)
    participant P as Postgres
    participant K as keystore (Redis)

    C->>G: POST /api/v1/auth/token {client_id, client_secret}
    G-->>C: access token (15 min) + refresh token (new family)
    C->>G: POST /api/v1/auth/session-key {client X25519 public key, repository ids}
    G->>K: SET xype:sk:<key_id> = wrap(KEK_session, session key), TTL 24 h
    G->>P: read wrapped repository keys (authorised repositories only)
    G-->>C: key_id, server public key, wrapped session key, wrapped repository keys
    C->>G: Session stream open (authorization: Bearer …)
    C->>G: Hello {session_key_id, repository_roots}
    G-->>C: HelloAck {session_id, heartbeat, rate limit}
```

## Initial index

```mermaid
sequenceDiagram
    autonumber
    participant W as Idle-time worker (client)
    participant C as xype-ai (client)
    participant G as Gateway (backend)
    participant Q as RabbitMQ
    participant I as Indexer
    participant K as keystore (Redis)
    participant E as Embedder (Ollama)
    participant P as Postgres + pgvector

    W->>W: skip denied paths, scrub whole file, chunk (tree-sitter), hmac_id, Merkle tree
    W->>C: chunks ready (in memory only)
    C->>G: ChunkUpload {ChunkRef[], EncryptedPayload[], new root, is_initial}
    G->>G: check principal, repository, key owner, key age and count, nonce counter
    G->>Q: index.chunks {ChunkRef[], ciphertext, key_id, AAD inputs}
    I->>Q: consume index.chunks
    I->>K: GET xype:sk:<key_id> (indexer ACL: GET only)
    I->>I: unwrap key, rebuild AAD, decrypt into SecretBytes (tag verified), scrub re-check
    I->>E: embed documents (internal models network)
    I->>I: wipe plaintext and key
    I->>Q: index.vectors {ChunkRef[], vectors, model id + version, rejected[]}
    G->>Q: consume index.vectors
    G->>P: VectorStore.upsert, MerkleIndex.apply (after every chunk settled)
    G-->>C: IndexAck {indexed chunk ids, rejected, server root hash}
```

## Query and selective fetch (Phase 4, outline)

```mermaid
sequenceDiagram
    autonumber
    participant C as xype-ai (client)
    participant G as Gateway / orchestrator
    participant P as Postgres + pgvector
    participant L as LLM worker (via RabbitMQ)

    C->>G: ChatQuery {scope, EncryptedPayload(query)}
    G->>G: embed the query (path decided in Phase 4)
    G->>P: VectorStore.search(authorised repository_ids, k)
    P-->>G: SearchHit[] (ChunkRef + score)
    G-->>C: ChunkRequest {ChunkRef[]} (obfuscated ids + line ranges)
    C->>C: serve only refs inside the query's scope, within count and byte caps
    C-->>G: ChunkResponse (encrypted, scrubbed)
    G->>L: prompt (transport decided in Phase 3–4)
    L-->>G: tokens
    G-->>C: ChatDelta stream (encrypted)
```

Open questions for Phases 3–4:
- how the query reaches the embedder, given that the backend is not on the `models` network;
- how prompts travel to the LLM worker over RabbitMQ without plaintext in the broker.
