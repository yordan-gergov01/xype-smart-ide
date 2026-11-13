# 0018 — Merkle tree over the directory structure, level-by-level delta sync

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §3.3 (`GET api/{codebase}/merkle-tree`), §3.4.2, §3.5 (Merkle sync)

## Context and problem statement

After the first index, a client and the server must agree on which chunks changed, without the client sending its whole workspace and without the server learning the structure in clear. Two things trigger a sync: debounced file changes and a 10-minute poll. The comparison should cost O(log n) per changed leaf (brief §3.5). The proto already defines `MerkleSyncRequest`, `MerkleTreeReport`, `MerkleDelta`, `ChunkUpload` and `IndexAck`, and the schema has `merkle_nodes`.

## Decision drivers

- Few round trips and little data for a typical change of one file and a few chunks.
- Identifiers and hashes reveal nothing without the repository key (ADR-0016).
- A sync is idempotent and safe to repeat after a dropped stream, and it always terminates.
- The client builds the tree in the idle-time worker (> 2 s debounce, CPU cap).

## Considered options

1. **A tree that mirrors the directories (root → directories → files → chunks); the server descends level by level, only into nodes whose hash differs.**
2. A flat list of file hashes, compared in one message.
3. A balanced binary tree over the sorted chunk hashes.

## Decision outcome

Chosen option: **1**.

### Nodes

- **Directories and files.** Each is a node with `node_id = hmac_id(repo_key, "dir" | "file", path)`.
- **Chunks.** Each is a leaf with `node_id = hmac_id(repo_key, "chunk-pos", path, chunk_kind, qualified_symbol, occurrence)`, framed as ADR-0016 defines.
  - A chunk with a symbol keeps its id when lines are inserted above it.
  - A chunk without a symbol (a block or line window) uses its ordinal in the file instead. Its id changes when a chunk is inserted before it, which costs a re-embed of the chunks that follow.
- **Leaf hash.** `hmac_id(repo_key, "chunk-content", scrubbed_text)`.
- **Inner hash.** SHA-256 over the children, sorted by `node_id`, each child serialised as `node_id ‖ hash ‖ is_leaf` (fixed 64 + 64 hex + 1 byte). The root hash is stored in `repositories.merkle_root_hash`.

### Protocol

Sequence diagram: `docs/flows/merkle-sync.md`.

1. **Root.** The client sends `Hello.repository_roots` on (re)connect, and a `MerkleRootReport` after a debounced change. The server also asks on its 10-minute poll with a `MerkleSyncRequest` whose `node_ids` is empty, meaning the root. If the roots are equal, the sync is done.
2. **Descend.** The server sends a `MerkleSyncRequest` listing the node ids to descend. The client answers with one `MerkleTreeReport` holding the children of all requested nodes, each tagged with its `parent_id`.
3. **Compare.** The server compares those children with its `merkle_nodes`. Children whose hash differs, or that the server does not know, go into the next request. Children the client no longer reports are deleted, but only once every page for that parent has arrived. A leaf whose hash is unchanged but whose line range moved gets its stored range updated, with no re-embed.
4. **Delta.** When only leaves differ, the server sends a `MerkleDelta` with the `ChunkRef`s to upload and the deleted node ids. The client answers with a `ChunkUpload` holding the encrypted chunks and its new root hash.
5. **Apply.** The server applies the new nodes and the root once every uploaded chunk has **settled**: its vector is written, or the indexer rejected it (scrub re-check, ADR-0019). Rejected chunks are stored as leaves without a vector and reported in `IndexAck.rejected`, so the next sync does not upload them again. A partial or failed sync leaves the old root, so the next sync repeats the work.

The gateway authorizes the `repository_id` of every sync message against the principal's team (ADR-0015).

### Cost

One round trip per tree level (directory depth + 2), carrying only the children of changed nodes. For one changed file that is O(depth × fan-out along the changed path), in practice logarithmic in the number of files.

### Limits

- At most 2 000 nodes per report and 256 chunks per upload; larger sets are paged.
- A sync has a deadline. If the stream drops, the server forgets the sync and the next trigger starts again from the root.

### Contract changes

These arrive in slice 6 (`Hello`) and slice 7 (index messages). They are additive, so `buf breaking` passes.
- `Hello.repository_roots`: a map from repository id to root hash.
- A new client message `MerkleRootReport {repository_id, root_hash}`.
- `MerkleNode.parent_id`.
- `IndexAck.rejected`: chunk refs with a fixed reason code.
- `ChunkRef.leaf_node_id`: the Merkle leaf's position id, the upsert and delete key of `code_chunks` (ADR-0020); carried in `ChunkUpload`, `index.chunks` and `index.vectors`.

### Implementation notes (slice 7b)

The contract changes above are in the proto: `MerkleRootReport` (a `SessionRequest` body, refused as not handled until slice 7c, after its repository check), `MerkleNode.parent_id`, `IndexAck.rejected` (`RejectedChunk` with a fixed `RejectionReason`) and `ChunkRef.leaf_node_id`. The leaf id is also bound into the payload AAD (ADR-0015, version 2).

### Consequences

- Good: a one-file edit costs a few small messages. Renaming a directory re-sends only that subtree.
- Good: it maps onto `merkle_nodes` (parent links) with small, additive contract changes.
- Bad: a very flat directory with thousands of files has a large fan-out at one level. Paging handles it, but that level costs more than O(log n).
- Bad: the server sees the shape of the tree (how many children each node has), though not names. This is recorded in `docs/privacy.md`.

### Confirmation

Unit tests with a fake `MerkleIndex`:
- identical trees sync in one message;
- one changed chunk gives exactly one upload;
- a deleted file deletes its subtree;
- inserting lines above a function updates its stored range and re-embeds nothing;
- a rejected chunk settles, and the next sync finds no difference;
- a dropped page deletes nothing;
- the number of round trips matches the tree depth.

End to end on the seeded sample repo (slice 12): editing one function re-embeds exactly that chunk.

## Pros and cons of the options

### 2. A flat list of file hashes

- Good: trivial to build.
- Bad: every sync sends every file's hash (O(n)), and a second step is still needed to find the changed chunks within a file.

### 3. A balanced binary tree over sorted chunk hashes

- Good: a strict O(log n) depth.
- Bad: one insertion shifts the whole tree, so deltas grow. It also loses file and directory boundaries, which the call graph and retrieval need.

## More information

- ADR-0016 (identifiers and framing), ADR-0015 (indexing flow), ADR-0019 (rejected chunks), `proto/xype/v1/index.proto`, `server/common_lib/models/index.py` (`merkle_nodes`).
