# Flow: Merkle delta sync

Brief §3.5. Decisions: ADR-0018 (tree and protocol), ADR-0016 (identifiers), ADR-0015 (upload path). Implemented in Phase 2.

Triggers: a debounced file change (> 2 s without typing) sends a `MerkleRootReport`; `Hello.repository_roots` on (re)connect; and the server's 10-minute poll (a `MerkleSyncRequest` with empty `node_ids`, meaning the root).

```mermaid
sequenceDiagram
    autonumber
    participant W as Idle-time worker (client)
    participant C as xype-ai (client)
    participant G as Gateway (backend)
    participant P as Postgres (merkle_nodes)

    W->>W: re-scrub and re-chunk changed files, update leaf and parent hashes
    C->>G: root hash (Hello, MerkleRootReport, or answer to the poll)
    G->>G: authorize repository for the principal's team
    G->>P: MerkleIndex.root(repository)
    alt roots equal
        G-->>C: nothing to do
    else roots differ
        loop one round trip per tree level
            G-->>C: MerkleSyncRequest {node_ids to descend}
            C->>G: MerkleTreeReport {children with parent_id}
            G->>G: check the level's hash against its parent's (the root first)
            G->>P: MerkleIndex.children(node_ids)
            G->>G: keep children whose hash differs or is unknown; keep leaves for moved line ranges
        end
        G->>P: MerkleIndex.apply(new root, nodes, deleted ids): new or changed leaves pending
        G-->>C: MerkleDelta {ChunkRef[] to upload, deleted node ids}
        C->>G: ChunkUpload {changed chunks, new root}
        Note over G: indexing path as in context-awareness.md
        G->>P: MerkleIndex.settle(result) per indexer result (index.vectors consumer)
        G-->>C: IndexAck {server root hash, rejected}
    end
```

The order above is the amended design (ADR-0018, slice 8c2): leaves needing a vector are persisted as pending, so nothing waits in a stream's memory. The store and the `index.vectors` consumer implement it from slice 8c2; the stream applies before it sends the delta from slice 8c3a, and the gateway accepts `ChunkUpload` from slice 8c3b.

## Failure handling

| Situation | Behaviour |
|---|---|
| Stream drops during a sync | Before the apply: the server forgets the sync and the old root stays. After it: pending leaves stay pending and are requested again (slice 8c3) |
| Indexer fails for some chunks | Their leaves stay pending; the server requests them again after the settle window (slice 8c3) |
| A result arrives for content a later sync replaced or deleted, or for another repository | It writes nothing and is dead-lettered `not_pending` (it waits for any apply in progress first) |
| The same result is delivered twice | It settles once; the repeat is acknowledged |
| A vector of another embedding model arrives | It writes nothing, is dead-lettered `wrong_model` (logged as a warning: the backend and indexer disagree), and its leaf stays pending for the re-request |
| A chunk fails the server scrub re-check | It settles as rejected, is stored as a leaf without a vector and is not uploaded again |
| Session key expired before indexing | The chunk goes to the dead-letter queue with a fixed reason; the next sync re-uploads it |
| Report above 2 000 nodes or upload above 256 chunks | Paged (`last_page`); nothing is compared or deleted before a level's last page |
| A level does not hash to its parent's hash (a skipped page or parent, or the tree changed) | Refused; the sync is forgotten and nothing is written; the client sends a new root report |
| Another sync of the repository applied first | The sync starts again from the root, at most three times |
