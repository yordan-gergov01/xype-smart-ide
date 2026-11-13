# Traceability: brief decisions → ADRs → code

Every architecture decision in [PROJECT_BRIEF.md](PROJECT_BRIEF.md) §3, the XYPE capabilities in §4 and the cost levers in §5 has a row. Status values: `planned (phase N)`, `in progress`, `implemented`, `deviation (ADR-NNNN)`. Rows marked `implemented` must link code **and** tests. Checked with the `traceability-check` skill.

| # | Decision (brief §) | Status | ADR | Code / tests |
|---|---|---|---|---|
| T1 | Codebase-wide context awareness (3.1) | planned (2) | — | — |
| T2 | Completion incl. FIM, P99 < 200 ms; completion stays local (3.1, 3.5, 5) | planned (3) | ADR-0003 | — |
| T3 | Chat scoped to files / full codebase, P99 < 3 s TTFT (3.1, 3.5) | planned (4) | — | — |
| T4 | Multi-file edits as diffs with accept/reject (3.1) | planned (5) | — | — |
| T5 | Background agent tasks (3.1, 3.5) | planned (5) | — | — |
| T6 | Scale estimates at 10× headroom (3.2) | in progress | — | [scale-estimates.md](scale-estimates.md) |
| T7 | REST `/api/v1` + gRPC bidi session stream (3.3) | in progress (contract, code generation, stream server with auth, Hello, heartbeat, token-expiry close and edge `grpc_pass`, revocation closing live streams and drain on shutdown done) | ADR-0013 | `proto/xype/v1/`, `server/devtools/gen_proto.py`, `server/backend/grpc/`, `server/backend/services/session/` (incl. `registry.py`), `server/backend/dal/session/`, `server/backend/dal/auth/subscriber.py`, `deploy/docker/edge/nginx.conf`; tests: `server/tests/unit/backend/test_grpc_stream.py`, `server/tests/unit/backend/test_stream_revocation.py`, `server/tests/unit/backend/test_session_gate.py`, `server/tests/unit/backend/test_stream_timers.py`; checks: `npm run proto`, CI `buf breaking` |
| T8 | Client credentials → access + refresh tokens, OIDC-ready (3.3) | planned (2) | ADR-0014 | — |
| T9 | IDE fork limited to branding/UX/bundling; AI in `xype-ai` (3.4.1) | planned (8) | ADR-0031 (planned) | rule: `.claude/rules/03-architecture.md` |
| T10 | AST chunking with web-tree-sitter (3.4.2) | in progress (grammars for all 7 languages load; chunking in slice 10) | ADR-0017 | `clients/xype-ai/grammars.json`, `clients/xype-ai/scripts/build-grammars.mjs`, `clients/xype-ai/src/context/grammars.ts`; tests: `clients/xype-ai/src/context/grammars.test.ts` |
| T11 | Call-graph dependent injection (3.4.2) | planned (2) | — | schema: `chunk_edges` in `server/common_lib/models/index.py` |
| T12 | Client Merkle tree + O(log n) delta sync, 10-min poll (3.4.2, 3.5) | planned (2) | ADR-0018 | flow: `docs/flows/merkle-sync.md` |
| T13 | Client AES-256-GCM, metadata obfuscation (3.4.2) | in progress (server side, repository keys and shared vectors done; client in slice 9) | ADR-0015, ADR-0016 | `server/common_lib/security/`, `server/backend/services/repositories/`, `server/backend/dal/repositories/`, `server/migrations/versions/0004_repository_keys.py`; tests: `server/tests/unit/backend/test_repositories.py`, `server/tests/unit/backend/test_session_keys.py`, `server/tests/integration/test_auth_stores.py`; `server/common_lib/keystore/`, `packages/crypto-vectors/vectors.json`; tests: `server/tests/unit/common_lib/test_security.py`, `server/tests/unit/common_lib/test_crypto_vectors.py`, `server/tests/integration/test_keystore.py` |
| T14 | Client secret/PII scrubbing before encryption (3.4.2, 3.7) | in progress (shared rules, corpus and server re-check done; client in slice 9) | ADR-0019 | `packages/scrub-rules/`, `server/common_lib/security/scrubber.py`; tests: `server/tests/unit/common_lib/test_scrubber.py` |
| T15 | Idle-time hashing: > 2 s debounce, 10–15% CPU cap (3.4.2) | planned (2) | — | — |
| T16 | Gateway: auth, rate limit 200/min, validation; in-memory decrypt for completion and chat, indexing decrypted in the indexer (3.4.3, 3.9) | in progress (REST auth, token families, rate limits, session keys, stream authentication and revocation closing live streams done) | ADR-0014, ADR-0015 (deviation, Proposed) | `server/backend/services/auth/`, `server/backend/dal/auth/`, `server/backend/api/auth.py`, `server/backend/admin_cli.py`, `server/backend/services/auth/session_keys.py`, `server/backend/dal/auth/subscriber.py`; tests: `server/tests/unit/backend/test_session_keys.py`, `server/tests/unit/backend/test_stream_revocation.py`, `server/tests/unit/backend/test_auth_service.py`, `server/tests/unit/backend/test_auth_api.py`, `server/tests/unit/backend/test_access_tokens.py`, `server/tests/integration/test_auth_stores.py` |
| T17 | Orchestrator: templates, guardrails, retrieval, routing, post-processing, fallbacks (3.4.4) | planned (3, 4, 5) | — | — |
| T18 | Intelligent router by task, prefs, budget, peak/off-peak (3.4.4, 5) | planned (3, 4, 5) | — | — |
| T19 | Stateless embedding, plaintext dropped after embedding (3.4.7) | in progress (embedder done; indexer Phase 2) | ADR-0002, ADR-0015 | `docs/privacy.md`, `server/common_lib/secret_bytes.py`, `server/common_lib/embeddings/`; tests: `server/tests/unit/common_lib/test_embeddings.py`, `server/tests/integration/test_ollama_embedder.py` |
| T20 | Postgres + pgvector HNSW behind `VectorStore` (3.4.6, 3.2) | in progress (schema and `VectorStore` done; wired with the indexer consumer in slice 8 and search in Phase 3) | ADR-0020, ADR-0035 | schema: `server/common_lib/models/index.py` (HNSW cosine, `vector(1024)`), `server/migrations/versions/0001_baseline_schema.py`, `0005_code_chunks_leaf_node_id.py`; `server/backend/services/retrieval/`, `server/backend/dal/retrieval/`, `server/common_lib/vectors.py`; tests: `server/tests/unit/backend/test_vector_store.py`, `server/tests/integration/test_vector_store.py` |
| T21 | Data model incl. XYPE tables, no plaintext code column (3.6) | implemented | ADR-0035 | `server/common_lib/models/`, `server/migrations/`; tests: `server/tests/integration/test_migrations.py` |
| T22 | Prompt templates as versioned files (3.6, 2) | planned (4) | ADR-0026 (planned) | rule: `.claude/rules/10-llm-prompts.md` |
| T23 | Privacy: in-memory decrypt, no plaintext at rest, memory hygiene, no-logging (3.7) | in progress (no-logging done; decrypt and memory hygiene in Phase 2) | ADR-0011, ADR-0015 | `server/common_lib/logging_config.py`, `server/backend/api/middleware.py`, `docs/privacy.md`; tests: `server/tests/unit/backend/test_privacy.py` |
| T24 | Privacy test suite (3.7) | planned (2) | — | — |
| T25 | Cache-aside Redis with specified key designs + embedding cache (3.8, 5) | planned (3) | ADR-0021 (planned) | — |
| T26 | Normalised cache keys (3.8) | planned (3) | ADR-0021 (planned) | — |
| T27 | Semantic cache, cosine > 0.95 (3.8) | planned (4) | ADR-0025 (planned) | — |
| T28 | Invalidation on source change and model/prompt version (3.8) | planned (3) | ADR-0021 (planned) | — |
| T29 | Race-to-response 200 ms + 100 ms (3.8) | planned (3) | ADR-0022 (planned) | — |
| T30 | Speculative decoding on vLLM, feature-flagged (3.8) | planned (3, gated) | ADR-0024 (planned) | — |
| T31 | Stateless services, read/write session split (3.8) | implemented | ADR-0009, ADR-0010, ADR-0035 | `server/backend/main.py`, `server/backend/core/connections.py`; tests: `server/tests/unit/backend/test_connections.py` |
| T32 | Strict timeouts with cancellation (3.9) | planned (3) | ADR-0023 (planned) | — |
| T33 | Circuit breaker per provider/model (3.9) | planned (3) | ADR-0023 (planned) | — |
| T34 | Retries: capped (interactive), exp. backoff + jitter, max 3 (async) (3.9) | planned (3, 5) | ADR-0023 (planned), ADR-0004 | — |
| T35 | Backup model chain, optional budget-aware fan-out (3.9) | planned (3) | — | schema: `llm_models.fallback_model_id` in `server/common_lib/models/llm.py` |
| T36 | Sync completion; async chat/agents via RabbitMQ with priority, DLQ, delayed retries, idempotency (3.9) | deviation (ADR-0004) | ADR-0004 | vhost with quorum default: `deploy/docker/rabbitmq/conf.d/20-xype.conf`, rendered by `deploy/docker/rabbitmq/entrypoint.sh` |
| T37 | Task events pushed over the session stream (3.9) | planned (5) | — | — |
| T38 | Reliability decision tree documented in `docs/reliability.md` (3.9) | planned (3) | ADR-0023 (planned) | — |
| T39 | Prompt engineering: JSON-schema outputs, examples, negative prompting, model variants (3.10) | planned (4) | ADR-0026 (planned) | — |
| T40 | Feedback loop (ratings, annotations) (3.10, 3.12) | planned (4) | — | schema: `feedback` in `server/common_lib/models/quality.py` |
| T41 | Post-processing, multi-model alternatives, compiler-as-a-judge (3.10) | planned (4, 7) | ADR-0030 (planned) | — |
| T42 | Eval harness: completion, chat, agentic, mutation, negative; CI gate (3.11) | planned (7) | ADR-0030 (planned) | — |
| T43 | Grafana dashboards, alerts, engagement metrics (3.12) | planned (7) | — | — |
| T44 | Prefix-cache-friendly prompt ordering (5) | planned (4) | ADR-0026 (planned) | rule: `.claude/rules/10-llm-prompts.md` |
| T45 | `CostScheduler` off-peak deferral (5) | planned (5) | ADR-0028 (planned), ADR-0005 | — |
| T46 | Budgets, degrade to cheaper models, hard kill switch (5) | planned (5) | — | — |
| T47 | Paid models only via disabled adapter + fixtures; free models in dev/CI (0, 5) | in progress (mock provider and guards done; paid adapter in Phase 10) | ADR-0005, ADR-0012 | `server/llm/providers/mock.py`; guards: `.claude/hooks/pre-bash-guard.mjs`, `tools/check_paid_api.py`, `tools/check_paid_config.py`, `server/tests/support/network_guard.py`; test: `server/tests/architecture/test_network_guard.py` |
| T48 | Minimized code transfer: only the context a request needs leaves the client (3.4.2, 3.7) | planned (2) | — | rule: `.claude/rules/07-security-privacy.md` |
| T49 | Local client cache for short-term completion recall (3.4.2) | planned (3) | — | — |
| T50 | Encryption in transit (TLS, local dev CA) and at rest (3.7) | in progress (TLS done for the edge proxy, backend, gRPC, Postgres, Redis, the key store and RabbitMQ; at rest before hosting) | ADR-0036 | `server/devtools/dev_ca.py`, `server/common_lib/tls.py`, `deploy/docker/`; tests: `server/tests/unit/common_lib/test_tls.py`, `server/tests/unit/devtools/test_dev_ca.py`, `server/tests/integration/test_probes.py`, `server/tests/unit/deploy/test_compose.py`; follow-up (at rest): `docs/plan.md` §3a |
| T51 | Model-inversion caveat documented; key rotation and scoped access (3.7) | in progress (caveat documented; rotation in Phase 2) | ADR-0015, ADR-0016 | `docs/privacy.md`; risk R6: `docs/plan.md` §4 |
| T52 | Agent tools that touch code run on the client via `ToolExecutor`, with approval rules (3.5) | planned (5) | ADR-0027 (planned) | — |
| T53 | Sequence diagrams for the five flows in `docs/` (3.5) | in progress (context awareness, Merkle sync) | ADR-0015, ADR-0018 | `docs/flows/context-awareness.md`, `docs/flows/merkle-sync.md` |
| T54 | Templates as first-class context, "New project from template" wizard, prefer template patterns (4.1) | planned (6) | — | — |
| T55 | Repo and shared rules packs injected as the stable prompt prefix (4.2, 5) | planned (6) | ADR-0026 (planned) | — |
| T56 | Project-context connector behind `ConnectorClient`, fake with fixtures until the API is shared (4.3) | planned (6) | ADR-0029 (planned) | — |
| T57 | Jira connector: my tickets, open, "Implement this ticket", status comment and transition (4.4) | planned (6) | ADR-0029 (planned) | — |
| T58 | Scope: only registered repositories and templates, no web search in v1 (4.5) | planned (2, 6) | — | rule: `.claude/rules/03-architecture.md` |
| T59 | Chat messages persisted encrypted (3.5) | planned (4) | — | schema: `chat_messages` in `server/common_lib/models/chat.py` |
| T60 | Scheduled jobs: index drift checks, eval runs, off-peak batches (3.4.5) | planned (5, 7) | ADR-0028 (planned) | — |
