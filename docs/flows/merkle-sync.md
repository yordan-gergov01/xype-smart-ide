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
            G->>P: MerkleIndex.children(node_ids)
            G->>G: keep children whose hash differs or is unknown; update moved line ranges
        end
        G-->>C: MerkleDelta {ChunkRef[] to upload, deleted node ids}
        C->>G: ChunkUpload {changed chunks, new root}
        Note over G: indexing path as in context-awareness.md
        G->>P: delete vectors and nodes of deleted ids (after all pages arrived)
        G->>P: MerkleIndex.apply(new root, nodes) once every chunk settled
        G-->>C: IndexAck {server root hash, rejected}
    end
```

## Failure handling

| Situation | Behaviour |
|---|---|
| Stream drops during a sync | The server forgets the sync; the old root stays; the next trigger starts from the root |
| Indexer fails for some chunks | The new root is not applied; the next sync finds the same differences |
| A chunk fails the server scrub re-check | It settles as rejected, is stored as a leaf without a vector and is not uploaded again |
| Session key expired before indexing | The chunk goes to the dead-letter queue with a fixed reason; the next sync re-uploads it |
| Report above 2 000 nodes or upload above 256 chunks | Paged; deletes wait for the last page |
