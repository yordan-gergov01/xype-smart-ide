# 0020 — Vector store: pgvector HNSW (cosine) behind `VectorStore`, iterative scans for repository filters

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §3.2, §3.4.6, §3.4.7

## Context and problem statement

Embeddings of code chunks (1024 dimensions, ADR-0002) must be stored and searched by cosine similarity. Every search is limited to the repositories that the caller's scope allows: one repository for chat scope, several for templates. The schema already has `code_chunks.vector_embedding vector(1024)` with an HNSW cosine index (`m = 16`, `ef_construction = 64`; ADR-0035). The brief names pgvector now and a hosted vector database (turbopuffer) as a later option.

## Decision drivers

- One store already in the stack, transactional with the chunk metadata.
- A repository filter must not silently cut results. With approximate indexes, pgvector applies filters after the index scan, so a selective filter can return fewer than `k` rows.
- A seam that a hosted vector database can replace without touching services.
- Size: about 300k chunks at the hosted 10× target, ≈ 1.23 GB of raw vectors plus the index (scale-estimates §3). The local stack (≤ 4 GB, scale-estimates §6) holds only the seeded sample and a few real repositories.

## Considered options

1. **pgvector HNSW in Postgres behind `VectorStore`. Search with `hnsw.iterative_scan = relaxed_order`, then re-sort the results.**
2. pgvector with one partial index per repository.
3. A separate vector database now (turbopuffer, Qdrant).

## Decision outcome

Chosen option: **1**.

- **Seam.** `VectorStore` (`docs/interfaces.md`, retrieval) with `upsert`, `delete` and `search(vector, repository_ids, k, min_score)`. The pgvector adapter lives in `backend/dal/retrieval/`. An in-memory fake (brute-force cosine) shares one contract suite with it. Services only see the Protocol.
- **Search.** One transaction:
  - `SET LOCAL hnsw.ef_search` (default 100, `VECTOR_EF_SEARCH`);
  - `SET LOCAL hnsw.iterative_scan = relaxed_order`;
  - `hnsw.max_scan_tuples` stays at its default of 20 000;
  - the query is `ORDER BY vector_embedding <=> :q LIMIT :k` with `WHERE repository_id = ANY(:ids) AND embedding_model_id = :m AND embedding_model_version = :v`;
  - because `relaxed_order` may return rows slightly out of distance order, the adapter re-sorts the `k` rows.

  The score is `1 - cosine_distance`, and `min_score` filters after that.
- **Upsert.**
  - The key is `(repository_id, leaf_node_id)`: the Merkle leaf's position id (ADR-0018), so a repeated delivery from the broker is idempotent (ADR-0004) and identical chunks in one repository or one file stay separate rows. A new `code_chunks.leaf_node_id` column replaces the unique constraint `(repository_id, encrypted_chunk_hash, obfuscated_filename)` through an Alembic revision in slice 7 (ADR-0035).
  - The model id and version are stored per row (ADR-0002). A search only matches rows from the current embedding model. A model change triggers a re-embed, never mixed vectors.
- **Delete.** By `repository_id` and leaf node ids, issued by the Merkle sync (ADR-0018); deleting one copy of an identical chunk never touches another. Deleting a repository cascades.
- **Writes.** Only the backend writes, through the write session (ADR-0035): the `index.vectors` consumer upserts and the Merkle sync deletes. Searches use the read session.
- **Later.** A turbopuffer adapter would pass the same contract suite. Its selection is an ADR when it is needed (not before hosting).

### Consequences

- Good: no new service. Vectors and metadata stay consistent in one transaction.
- Good: iterative scans keep recall for selective repository filters without one index per repository.
- Bad: HNSW builds and inserts are CPU and memory heavy on the dev machine. The first index of a large repository is slow (R7), so memory and timing are measured in slice 8.
- Bad: `relaxed_order` gives up exact ordering; the adapter's re-sort restores it within the returned rows.

### Confirmation

- The contract suite runs against the fake and against pgvector in testcontainers (slice 7). It checks:
  - upsert idempotency;
  - delete;
  - that a repository filter never returns rows from another repository;
  - recall@10 ≥ 0.95 against brute force on a seeded set with a selective filter;
  - that rows from another model version are ignored.
- The privacy test asserts that `code_chunks` holds only HMACs, line ranges, enums and vectors.

## Pros and cons of the options

### 2. A partial index per repository

- Good: exact filtering per index.
- Bad: one index per repository is a migration per registration and does not cover searches across several repositories.

### 3. A separate vector database now

- Good: built for scale and filtering.
- Bad: one more container on an 8 GB machine, and two stores to keep consistent. A hosted service also means code-derived data leaves our infrastructure, which needs its own privacy review.

## More information

- pgvector README: iterative index scans (since 0.8.0), `hnsw.ef_search` (default 40), filtering guidance.
- ADR-0002 (model and dimension), ADR-0035 (schema and sessions), ADR-0018 (deletes from sync).
