# Traceability: brief decisions → ADRs → code

Every architecture decision in [PROJECT_BRIEF.md](PROJECT_BRIEF.md) §3 (plus the cost levers in §5) has a row. Status values: `planned (phase N)`, `in progress`, `implemented`, `deviation (ADR-NNNN)`. Rows marked `implemented` must link code **and** tests. Checked with the `traceability-check` skill.

| # | Decision (brief §) | Status | ADR | Code / tests |
|---|---|---|---|---|
| T1 | Codebase-wide context awareness (3.1) | planned (2) | — | — |
| T2 | Completion incl. FIM, P99 < 200 ms (3.1, 3.5) | planned (3) | ADR-0003 | — |
| T3 | Chat scoped to files / full codebase, P99 < 3 s TTFT (3.1, 3.5) | planned (4) | — | — |
| T4 | Multi-file edits as diffs with accept/reject (3.1) | planned (5) | — | — |
| T5 | Background agent tasks (3.1, 3.5) | planned (5) | — | — |
| T6 | Scale estimates at 10× headroom (3.2) | in progress | — | [scale-estimates.md](scale-estimates.md) |
| T7 | REST `/api/v1` + gRPC bidi session stream (3.3) | in progress (contract done; servicer Phase 2) | ADR-0013 | `proto/xype/v1/`; checks: `npm run proto`, CI `buf breaking` |
| T8 | Client credentials → access + refresh tokens, OIDC-ready (3.3) | planned (2) | ADR-0014 (planned) | — |
| T9 | IDE fork limited to branding/UX/bundling; AI in `xype-ai` (3.4.1) | planned (8) | ADR-0031 (planned) | rule: `.claude/rules/03-architecture.md` |
| T10 | AST chunking with web-tree-sitter (3.4.2) | planned (2) | ADR-0017 (planned) | — |
| T11 | Call-graph dependent injection (3.4.2) | planned (2) | — | draft: `chunk_edges` table |
| T12 | Client Merkle tree + O(log n) delta sync, 10-min poll (3.4.2, 3.5) | planned (2) | ADR-0018 (planned) | — |
| T13 | Client AES-256-GCM, metadata obfuscation (3.4.2) | planned (2) | ADR-0015, ADR-0016 (planned) | — |
| T14 | Client secret/PII scrubbing before encryption (3.4.2, 3.7) | planned (2) | ADR-0019 (planned) | — |
| T15 | Idle-time hashing: > 2 s debounce, 10–15% CPU cap (3.4.2) | planned (2) | — | — |
| T16 | Gateway: auth, in-memory decrypt, rate limit 200/min, validation (3.4.3, 3.9) | planned (2) | — | — |
| T17 | Orchestrator: templates, guardrails, retrieval, routing, post-processing, fallbacks (3.4.4) | planned (3–5) | — | — |
| T18 | Intelligent router by task, prefs, budget, peak/off-peak (3.4.4, 5) | planned (3–5) | — | — |
| T19 | Stateless embedding, plaintext dropped after embedding (3.4.7) | in progress (embedder done; indexer Phase 2) | ADR-0002 | `server/common_lib/secret_bytes.py`, `server/common_lib/embeddings/`; tests: `server/tests/unit/common_lib/test_embeddings.py`, `server/tests/integration/test_ollama_embedder.py` |
| T20 | Postgres + pgvector HNSW behind `VectorStore` (3.4.6, 3.2) | planned (1–2) | ADR-0020 (planned), ADR-0035 | schema: `server/common_lib/models/index.py` (HNSW cosine, `vector(1024)`), `server/migrations/versions/0001_baseline_schema.py`; `VectorStore` in Phase 2 |
| T21 | Data model incl. XYPE tables, no plaintext code column (3.6) | implemented | ADR-0035 | `server/common_lib/models/`, `server/migrations/`; tests: `server/tests/integration/test_migrations.py` |
| T22 | Prompt templates as versioned files (3.6, 2) | planned (4) | ADR-0026 (planned) | rule: `.claude/rules/10-llm-prompts.md` |
| T23 | Privacy: in-memory decrypt, no plaintext at rest, memory hygiene, no-logging (3.7) | in progress (no-logging done; decrypt and memory hygiene in Phase 2) | ADR-0011 | `server/common_lib/logging_config.py`, `server/backend/api/middleware.py`; tests: `server/tests/unit/backend/test_privacy.py` |
| T24 | Privacy test suite (3.7) | planned (2) | — | — |
| T25 | Cache-aside Redis with specified key designs + embedding cache (3.8) | planned (3) | ADR-0021 (planned) | — |
| T26 | Normalised cache keys (3.8) | planned (3) | ADR-0021 (planned) | — |
| T27 | Semantic cache, cosine > 0.95 (3.8) | planned (4) | ADR-0025 (planned) | — |
| T28 | Invalidation on source change and model/prompt version (3.8) | planned (3) | ADR-0021 (planned) | — |
| T29 | Race-to-response 200 ms + 100 ms (3.8) | planned (3) | ADR-0022 (planned) | — |
| T30 | Speculative decoding on vLLM, feature-flagged (3.8) | planned (3, gated) | ADR-0024 (planned) | — |
| T31 | Stateless services, read/write session split (3.8) | implemented | ADR-0009, ADR-0010, ADR-0035 | `server/backend/main.py`, `server/backend/core/connections.py`; tests: `server/tests/unit/backend/test_connections.py` |
| T32 | Strict timeouts with cancellation (3.9) | planned (3) | ADR-0023 (planned) | — |
| T33 | Circuit breaker per provider/model (3.9) | planned (3) | ADR-0023 (planned) | — |
| T34 | Retries: capped (interactive), exp. backoff + jitter, max 3 (async) (3.9) | planned (3, 5) | ADR-0023, ADR-0004 | — |
| T35 | Backup model chain, optional budget-aware fan-out (3.9) | planned (3) | — | draft: `llm_models.fallback_model_id` |
| T36 | Sync completion; async chat/agents via RabbitMQ with priority, DLQ, delayed retries, idempotency (3.9) | deviation (ADR-0004) | ADR-0004 | vhost with quorum default: `deploy/docker/rabbitmq/definitions.json` |
| T37 | Task events pushed over the session stream (3.9) | planned (5) | — | — |
| T38 | Reliability decision tree documented in `docs/reliability.md` (3.9) | planned (3) | ADR-0023 (planned) | — |
| T39 | Prompt engineering: JSON-schema outputs, examples, negative prompting, model variants (3.10) | planned (4) | ADR-0026 (planned) | — |
| T40 | Feedback loop (ratings, annotations) (3.10, 3.12) | planned (4) | — | draft: `feedback` table |
| T41 | Post-processing, multi-model alternatives, compiler-as-a-judge (3.10) | planned (4, 7) | ADR-0030 (planned) | — |
| T42 | Eval harness: completion, chat, agentic, mutation, negative; CI gate (3.11) | planned (7) | ADR-0030 (planned) | — |
| T43 | Grafana dashboards, alerts, engagement metrics (3.12) | planned (7) | — | — |
| T44 | Prefix-cache-friendly prompt ordering (5) | planned (4) | ADR-0026 (planned) | rule: `.claude/rules/10-llm-prompts.md` |
| T45 | `CostScheduler` off-peak deferral (5) | planned (5) | ADR-0028 (planned), ADR-0005 | — |
| T46 | Budgets, degrade to cheaper models, hard kill switch (5) | planned (5) | — | — |
| T47 | Paid models only via disabled adapter + fixtures; free models in dev/CI (0, 5) | in progress (mock provider and guards done; paid adapter in Phase 10) | ADR-0005, ADR-0012 | `server/llm/providers/mock.py`; guards: `.claude/hooks/pre-bash-guard.mjs`, `tools/check_paid_api.py`, `tools/check_paid_config.py`, `server/tests/support/network_guard.py`; test: `server/tests/architecture/test_network_guard.py` |
