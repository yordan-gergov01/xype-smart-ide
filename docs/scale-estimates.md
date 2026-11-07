# Scale estimates

Status: Phase 0 draft (2026-11-07). Every number here is an estimate. Phase 3 and Phase 7 replace them with measured benchmarks.

Method: back-of-the-envelope estimation. Start from users and requests, derive throughput, storage and cost, then add headroom. Inputs: 30 completions per dev per peak hour, 5 chat queries and 20 agent tasks per dev per day (brief §3.2), 2× safety margin, ~15 KB payload per completion (≈ 3 000 tokens of code + history). All developers are assumed to be active at peak at the same time (conservative at this team size).

Local hardware (Q5): CPU only, **8 GB RAM**. See §6 for the memory budget.

## 1. Users

| | Today | Design target (10×) |
|---|---|---|
| Developers | 6 | 60 |
| Repositories (incl. templates) | 15 | 30 |
| Concurrent persistent gRPC streams | ≤ 6 | ≤ 60 (one per IDE window; assume ≤ 2 windows/dev → 120) |

Safety margin on top of the 10×: **2×**.

## 2. Request load

### 2.1 Code completion (sync, latency-critical)

- 6 devs × 30 requests per peak hour = 180 per hour = **0.05 RPS**.
- × 10 headroom = 0.5 RPS; × 2 safety = **1 RPS**. Burst factor of 2 (several devs typing at once) → design for **2 RPS sustained**.
- Bandwidth: 2 RPS × 15 KB = **30 KB/s** inbound. Network is not a constraint; latency is.

Risk: 30 per hour is low compared with modern editors that fire on every typing pause. Those send roughly 5–10 per minute, i.e. 300–600 per hour per dev. The client cache and the 2 s debounce cut the server-side rate, but a realistic worst case is ~10× the assumed rate, which means **≈ 10 RPS at the 10× target**. A CPU-only local 1.5B FIM model cannot serve 10 RPS within P99 < 200 ms. The Phase 3 benchmark must measure it. Mitigations in order: client cache, server boilerplate cache, a GPU host or vLLM, request cancellation on new keystroke (most requests are cancelled before completion).

### 2.2 Chat (async through the queue, streamed)

- 6 devs × 5 per day = 30 per day today; **300 per day** at 10×.
- Peak hour holds ~25% of the day → 75 per hour ≈ 0.02 RPS. The constraint is time-to-first-token, not throughput.
- Average turn: ~8k input tokens (≈ 6k stable prefix + 2k retrieved chunks and query), ~800 output tokens.

### 2.3 Agent tasks (async, long-running)

- 6 devs × 20 per day = 120 per day today; **1 200 per day** at 10×.
- Average task: ~10 LLM turns; each ~20k input tokens (≈ 70% cache hit after turn 1), ~2k output tokens; ~10 client-executed tool calls.
- Concurrency: if a task takes ~5 minutes and 40% of the day's tasks land in a 2-hour window, at 10×: 480 tasks / 120 min × 5 min ≈ **20 concurrent agent tasks**. Size the worker pool for 20–30 concurrent tasks (they are I/O bound: LLM calls and waits on client tools).

### 2.4 Indexing and Merkle sync

- Delta sync after each debounced burst of edits: ~1 per dev per minute while active, each touching 1–5 chunks. At 10×: 60 per minute = **1 RPS of small delta uploads**, 1–5 embeddings each.
- 10-minute consistency poll: 120 streams / 600 s = 0.2 RPS of tree-hash comparisons (cheap: root hash compare, O(log n) descent only on mismatch).

## 3. Index and storage

### 3.1 Vectors

Embedder: Qwen3-Embedding-0.6B (ADR-0002), **1024 dims** by default, configurable down to 768 via MRL.

| Item | Formula | 1024 dims | 768 dims |
|---|---|---|---|
| Chunks | 30 repos × ≤ 10k | 300 000 | 300 000 |
| Heap storage per vector | 4 × d + 8 B (pgvector `vector`) | 4 104 B | 3 080 B |
| Raw vectors | 300k × above | **1.23 GB** | 0.92 GB |
| HNSW index | vectors are copied into the index pages, plus neighbour lists: layer 0 has up to 2·m = 32 neighbours × 6 B TIDs ≈ 0.2 kB/row, upper layers add little | **≈ 1.3–1.4 GB** | ≈ 1.0 GB |
| Metadata row (ids, hashes, line range, timestamps) | ~200 B | 60 MB | 60 MB |
| **Total on disk** | | **≈ 2.7 GB** | ≈ 2.0 GB |

- With a `halfvec` expression index the index halves (≈ 0.7 GB at 1024 dims) at a small recall cost. Keep it as a Phase 2 option, decided by measured recall.
- HNSW in pgvector supports up to 2 000 dims for `vector` and 4 000 for `halfvec`. The 4B (2560) and 8B (4096) Qwen3 embedders would therefore need `halfvec` (4B) or quantization / binary (8B).
- Build memory: the graph should fit in `maintenance_work_mem`; set ~2 GB for the initial build and `--shm-size` ≥ that in Docker for parallel builds (pgvector README).
- Postgres RAM target for the 10× case: 4 GB `shared_buffers`-friendly host (index fits in memory). Comfortable on a single node; read replica supported but not needed.

### 3.2 Embedding throughput (CPU)

- Assumption to verify in Phase 2: Qwen3-Embedding-0.6B on a modern 8-core CPU through TEI handles roughly **10–30 chunks per second** at ~256 tokens per chunk.
- Initial index of 300k chunks: 300 000 / 20 ≈ 15 000 s ≈ **4 hours** (one-off, run off-hours, resumable).
- Steady state: ~1 RPS × ≤ 5 chunks = ≤ 5 chunks/s, within budget. The embedding cache keyed by chunk hash removes re-embeds of unchanged chunks.
- Risk: if CPU throughput is far below this, fall back to 768 dims (same model, MRL), batch larger, or add a GPU host. It is a configuration change, not a code change.

### 3.3 Relational data (per month at 10×)

| Table | Volume | Size |
|---|---|---|
| `chat_messages` (encrypted) | 300 chats/day × 10 msgs × 22 days = 66k rows | ~150 MB (≈ 2 kB ciphertext each) |
| `agent_tasks` + `events` | 1 200 × 22 = 26k tasks, ~20 events each | ~100 MB |
| `cost_ledger` | one row per LLM call: (300×2 + 1 200×10) × 22 ≈ 280k | ~60 MB |
| `merkle_nodes` | ~2× files+chunks per repo: ~1M rows total | ~150 MB |

Total relational growth ≈ 0.5 GB/month at 10×. Retention policies (Phase 5) keep it bounded.

### 3.4 Redis

- Completion and explanation cache entries are small (≤ 4 kB). 100k entries ≈ 400 MB worst case; a 512 MB `maxmemory` with `allkeys-lru` is enough.
- Semantic cache: query vector (4 kB) + answer per entry; capped at ~20k entries ≈ 100 MB.
- Embedding cache: 1024-dim float32 = 4 kB per chunk. Caching all 300k chunks would be 1.2 GB, so cache only the hot set (recent delta-sync chunks) with TTL; Postgres is the source of truth.

### 3.5 RabbitMQ

- ≤ 2 000 messages/day at 10×, peak concurrency ~30. A single node with quorum queues is far below any limit (ADR-0004).

## 4. Latency budget

### 4.1 Completion: P99 < 200 ms

| Step | Budget |
|---|---|
| Client: collect context, scrub, encrypt | 10 ms |
| Network + gRPC (existing stream, local/LAN) | 10 ms |
| Gateway: auth (cached token), decrypt, rate-limit | 5 ms |
| Orchestrator: cache lookup (Redis) + prompt build | 10 ms |
| Model inference (FIM, ~32–64 output tokens) | **≤ 150 ms** |
| Post-processing + return | 10 ms |
| Slack | 5 ms |

The model step dominates. Race-to-response (primary at 200 ms → backup, wait ≤ 100 ms more) is a *tail* mechanism and implies an upper bound of ~300 ms for the slow path; the P99 target must hold on the normal path.

### 4.2 Chat: P99 < 3 s to first useful token

Query embed (≤ 100 ms) + vector search (≤ 50 ms) + selective fetch from client over the stream (≤ 300 ms) + prompt build (≤ 20 ms) + queue hop (≤ 50 ms) + model TTFT (≤ 2 s) → ≈ 2.5 s.

## 5. Cost (paid models, Phase 10 only)

Paid access goes through OpenRouter (ADR-0005). Two reference price sets, checked on 2026-11-07, all USD per 1M tokens.

**OpenRouter, `deepseek/deepseek-v4-pro`**, cheapest listed host (StreamLake): input $0.82, output $1.64; cache reads assumed at 0.1× input ($0.082) per OpenRouter's DeepSeek caching docs (per-host cache support to verify in Phase 10). No peak/off-peak split. OpenRouter adds no inference markup; a credit-purchase fee applies (rate to verify).

| Workload (Pro via OpenRouter, per unit) | Cost |
|---|---|
| Chat turn: 6k hit + 2k miss + 0.8k out | $0.0034 |
| Agent task: 10 × (14k hit + 6k miss + 2k out) | $0.093 |

| Monthly (22 working days) | Today (6 devs) | 10× (60 devs) |
|---|---|---|
| Chat: 660 / 6 600 turns | ≈ $2 | ≈ $22 |
| Agents: 2 640 / 26 400 tasks | ≈ $247 | ≈ $2 470 |

**DeepSeek direct** (api-docs.deepseek.com; **off-peak** shown, peak is 2×) — kept as the comparison baseline:

| Model | Input cache hit | Input cache miss | Output |
|---|---|---|---|
| `deepseek-v4-pro` | $0.022 | $0.66 | $1.98 |
| `deepseek-flash` | $0.003 | $0.15 | $0.60 |

Peak windows: 01:00–04:00 and 06:00–10:00 UTC, Monday–Friday (excluding Chinese public holidays).

| Workload (Pro, per unit) | Off-peak | Peak |
|---|---|---|
| Chat turn: 6k hit + 2k miss + 0.8k out | $0.0030 | $0.0061 |
| Agent task: 10 × (14k hit + 6k miss + 2k out) | $0.082 | $0.165 |

| Monthly (22 working days) | Today (6 devs) | 10× (60 devs) |
|---|---|---|
| Chat: 660 / 6 600 turns | $2–4 | $20–40 |
| Agents: 2 640 / 26 400 tasks | $217–435 | $2 170–4 350 |

Conclusions:
- Agent tasks dominate cost by ~100×. The router's degrade-to-Flash path matters far more than chat optimisation.
- Via OpenRouter, a task costs about the same as DeepSeek direct off-peak (≈ $0.09 vs $0.08) and about half of DeepSeek direct peak ($0.165). The `CostScheduler` off-peak deferral only saves money on the direct profile; on OpenRouter it still smooths load.
- The cache-miss share of the prompt dominates input cost (30× the hit price), so byte-identical stable prefixes are the biggest single lever.
- Completion stays on the local model: $0.
- These are planning numbers; Phase 10 produces a measured cost report.

## 6. Local memory budget (8 GB RAM, CPU only)

The 10× numbers above describe the future hosted environment. The dev machine has 8 GB in total, shared by Windows, the editor and Docker Desktop (WSL2). Target: keep the default compose stack **≤ 4 GB**.

| Service | Estimated RAM | Note |
|---|---|---|
| Ollama: `qwen2.5-coder:1.5b` (≈ 1 GB) + `qwen3-embedding:0.6b` (≈ 0.64 GB) + KV cache | ~2.0 GB | one runtime serves both models (ADR-0002) |
| PostgreSQL 16 + pgvector | 0.5 GB | dev index is small; `shared_buffers=256MB` |
| RabbitMQ 4.4 | 0.25 GB | |
| Redis | 0.1 GB | `maxmemory 64mb` locally |
| api + grpc + worker + scheduler (Python) | 0.6 GB | ~150 MB each |
| **Default total** | **≈ 3.5 GB** | |
| Observability profile: Prometheus, Grafana, OTel collector | +0.5 GB | `docker compose --profile observability` |
| TEI CPU with fp32 Qwen3-Embedding-0.6B | +2.5 GB | **not** in the local default; hosted environment only |

Consequences:
- Docker Desktop's WSL2 memory limit set to ~4–5 GB (`.wslconfig`), documented in the runbook.
- 7B models and the full Code-OSS fork build (8 GB recommended for the build alone) do not fit locally. The fork is built on GitHub Actions runners (Phase 8).
- The completion latency target is measured on this machine in Phase 3, with the honest result reported.
