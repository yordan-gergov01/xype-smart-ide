# 0002 — Local embedding model: Qwen3-Embedding-0.6B (Ollama locally, TEI hosted)

- Status: Proposed
- Date: 2026-11-07
- Brief reference: §1 Embeddings, §3.4.7, §5

## Context and problem statement

Embeddings must come from a free, local, open-source, permissively licensed model that runs on CPU in its own container, with a configurable dimension. They drive codebase retrieval, the semantic cache and the embedding cache.

## Decision drivers

- Licence: permissive (Apache-2.0 or MIT), commercial use allowed.
- Code-retrieval quality.
- CPU feasibility for ~300k chunks (scale-estimates §3.2).
- Dimension ≤ 2 000 so pgvector HNSW works on plain `vector`.
- Serving through a standard, maintained inference server.

## Considered options

1. **Qwen3-Embedding-0.6B** — Apache-2.0, 0.6B params, up to 1024 dims with MRL (32–1024), 32K context, instruction-aware queries.
2. jina-embeddings-v2-base-code — Apache-2.0, 161M params, 768 dims, 8K context; needs `trust_remote_code`.
3. Qwen3-Embedding-4B / 8B — Apache-2.0, 2560 / 4096 dims; too heavy for CPU and above the 2 000-dim `vector` HNSW limit.

## Decision outcome

Chosen option: **Qwen3-Embedding-0.6B**, decided by the project owner in Phase 0 review ("use the better model").

Serving, behind the same `Embedder` interface:
- **Local default (8 GB RAM dev machine):** Ollama `qwen3-embedding:0.6b` (≈ 639 MB quantized) through `/api/embed` with the `dimensions` option, sharing the Ollama runtime with the FIM model. This saves ~2 GB compared with TEI fp32 (scale-estimates §6).
- **Hosted environment:** Hugging Face Text Embeddings Inference (`ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`, which lists Qwen3-Embedding as supported).
- Both adapters pass the same contract tests. Vectors from the quantized and fp32 variants are not mixed: the model version (incl. quantization) is stored per chunk and is part of the embedding-cache key, so switching servers triggers a re-embed.

- Default dimension **1024** (`EMBEDDER_DIM`), MRL truncation to 768 or 512 allowed by config; the migration reads the value at creation time. Changing it later means a full re-embed.
- Queries are embedded with the instruction prefix `Instruct: <task>\nQuery:<query>`; documents (code chunks) without it. The task string is a versioned prompt file.
- The `Embedder` interface returns the model ID + version, which are part of the embedding-cache key.
- Fallback for very weak machines: the same model at 768 dims, or the Ollama `/api/embed` adapter. Both stay behind `Embedder`.

### Consequences

- Good: stronger multilingual and code retrieval than smaller BERT-style models; Apache-2.0; one model family with the completion model.
- Good: upgrade path to 4B via `halfvec` (≤ 4 000 dims) without touching the core.
- Bad: ~4× more compute per chunk than a 161M model; initial indexing on CPU is slow (≈ 4 h estimate for 300k chunks).
- Bad: 1024 dims → ~33% bigger index than 768.

### Confirmation

Phase 2 benchmark: chunks/s on the dev machine, and recall@10 on a seeded retrieval set from our template repos; compare 1024 vs 768 dims. If CPU throughput < 5 chunks/s, revisit.

## More information

- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- https://github.com/huggingface/text-embeddings-inference
- https://ollama.com/library/qwen3-embedding (tag `0.6b`, 639 MB)
- pgvector HNSW dimension limits: https://github.com/pgvector/pgvector
