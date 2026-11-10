# XYPE IDE — Delivery plan

Status: Phase 0 draft (2026-11-07), awaiting review. Source of requirements: [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md).

Related Phase 0 outputs:
- [ADR index](adr/README.md)
- [Scale estimates](scale-estimates.md)
- [gRPC contract](../proto/xype/v1/) (ADR-0013)
- [DB schema](../server/common_lib/models/) and [migrations](../server/migrations/) (ADR-0035)
- [Interfaces](interfaces.md)
- [Open questions](questions.md)

## 1. Verified technology baseline (2026-11-07)

Versions are pinned in Phase 1 from these checks; nothing below comes from memory.

| Area | Verified fact | Source |
|---|---|---|
| Code-OSS build | Node **24.22.1** (`.nvmrc`), VS 2026 Build Tools with C++ workload, Win11 SDK 26100, Spectre-mitigated libs, Python + setuptools; 6 GB RAM minimum, 8 GB recommended | github.com/microsoft/vscode/wiki/How-to-Contribute |
| VSCodium approach | Git Bash, jq, 7-Zip, Python 3.11, rustup; `dev/build.sh` (flags `-l -o -p -s`), WiX v3 only for `.msi` | github.com/VSCodium/vscodium/blob/master/docs/howto-build.md |
| gRPC | `grpcio` **1.85.1** (Python ≥ 3.10), `grpc.aio` | pypi.org/project/grpcio |
| buf | `buf.yaml` **v2**; lint `STANDARD`, breaking `FILE` | buf.build/docs/configuration/v2/buf-yaml |
| pgvector | **0.8.7**, image `pgvector/pgvector:0.8.7-pg16`; HNSW ≤ 2 000 dims (`vector`), ≤ 4 000 (`halfvec`); defaults m = 16, ef_construction = 64; Python package `pgvector` 0.5.1 with SQLAlchemy `VECTOR` | github.com/pgvector/pgvector, pypi.org/project/pgvector |
| RabbitMQ | **4.4**; quorum queues are the recommended default; priorities 0–31 built in, strict since 4.3, `x-max-priority` ignored; at-least-once dead-lettering; `delivery-limit`; built-in delayed delivery via `x-opt-delivery-time` | rabbitmq.com/docs/priority, /quorum-queues |
| aio-pika | **10.2.2** (Python ≥ 3.11) | pypi.org/project/aio-pika |
| Ollama | FIM through `suffix` on `/api/generate`; `raw`, `keep_alive`; embeddings on `/api/embed` | github.com/ollama/ollama docs/api.md |
| FIM model | `qwen2.5-coder:1.5b` base, **Apache-2.0**. The 3B size is under the *qwen-research* licence, so it is excluded. 7B (Apache-2.0) is the stronger option | huggingface.co/Qwen |
| Embeddings | `Qwen/Qwen3-Embedding-0.6B`, **Apache-2.0**, up to 1024 dims (MRL 32–1024), 32K context, instruction-aware queries; served by TEI `cpu-1.9` | huggingface.co/Qwen/Qwen3-Embedding-0.6B, github.com/huggingface/text-embeddings-inference |
| Code-OSS release | **1.145.2** (2026-11-06) | api.github.com/repos/microsoft/vscode/releases/latest |
| Ollama release | **v0.40.15** (2026-11-03) | api.github.com/repos/ollama/ollama/releases/latest |
| web-tree-sitter | **0.27.1**; `import { Parser, Language } from 'web-tree-sitter'`, `await Parser.init()`, `Language.load(wasm)`; ABI 13–15 since 0.25; old prebuilt grammar `.wasm` files may need a rebuild with the current CLI | tree-sitter `lib/binding_web/README.md` |
| Paid LLM (OpenRouter) | `deepseek/deepseek-v4-pro` available via ~13 third-party providers (input $0.82–1.91, output $1.50–3.83 per 1M); no inference markup; ZDR per request (`provider.zdr`) or per account; automatic DeepSeek prefix caching (reads 0.1× input) with sticky provider routing; no off-peak pricing shown | openrouter.ai model page and docs |
| DeepSeek direct (reference) | Model IDs `deepseek-v4-pro` and **`deepseek-flash`** (`deepseek-v4-flash` is a retired alias); OpenAI-compatible `https://api.deepseek.com`; off-peak = 50% of peak. Prices: see scale-estimates §5 | api-docs.deepseek.com |

## 2. Milestones

Every phase ends at ⛔ with the report from brief §10. Exit criteria are in addition to the brief's definition of done.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0 Plan** | This document set | Reviewed and questions answered |
| **1 Foundations** | `.claude/` (rules, skills, agents, hooks, settings), `CLAUDE.md`, commitlint, pre-commit; server and client layout from the XYPE templates (ADR-0006), pip + hashed requirements, npm workspaces; docker compose (pg16+pgvector, Redis, RabbitMQ 4.4, Ollama, OTel collector, Prometheus, Grafana; TEI hosted only, ADR-0002); config, logging, OTel, metrics; storage + Alembic baseline; LLM, embedder and clock seams with fakes and shared contracts (other interfaces with the phase that gives them a real adapter, `docs/interfaces.md`); mock LLM; local embedder adapter; CI with paid-API guard, size and duplication checks, import boundaries, `buf lint` | `docker compose up` healthy; CI green; network guard proven by a failing test; no file > 500 lines |
| **2 Indexing & context** | `xype-ai` context engine (tree-sitter chunking for 7 languages, call graph, Merkle tree, AES-256-GCM, obfuscation, scrubber, idle-time worker); gRPC `Session` stream; auth + rate limiting; stateless embedding; pgvector index; Merkle delta sync; privacy test suite | Index a real template repo end to end; privacy suite proves no plaintext in Postgres, Redis, logs, traces |
| **3 Completion** | Sync FIM path; completion + boilerplate caches; timeouts, capped retry, circuit breaker, race-to-response | Benchmark report with measured P50/P99 on local hardware vs 200 ms |
| **4 Chat** | Scope selection, retrieval, selective client fetch, streaming, encrypted persistence, semantic cache, feedback, chat webview with design tokens | Chat TTFT measured; feedback stored; e2e test on mock LLM |
| **5 Agents & queue** | Quorum queues, priorities, DLQ, delayed retries, `events`; agent loop with client-executed tools and approval rules; multi-file diffs; `CostScheduler` | Kill-a-worker test: task resumes; DLQ after 3 retries; approval gating test |
| **6 Connectors** | Template registry + "New project" wizard; rules packs; Jira + Company OS fakes; "Implement ticket" flow | Ticket → agent → diff → Jira comment on mock model |
| **7 Evals & observability** | Eval harness (completion, chat, agentic, mutation, negative prompts), CI eval gate with baselines; Grafana dashboards + alerts; admin dashboard; k6/Locust at 10× | Gate fails on injected regression; load test report |
| **8 Fork & branding** | Code-OSS fork via VSCodium-style scripts, `product.json` (Open VSX, telemetry off), themes from design tokens, XYPE home, bundled `xype-ai`, Windows installer | Installer runs on a clean Windows machine; patch set documented and rebaseable |
| **9 Fine-tuning** | Colab notebooks, QLoRA, GGUF export, honest eval report | Base vs fine-tuned numbers on our completion evals |
| **10 Paid-model go-live** (DeepSeek V4 via OpenRouter) | Only after explicit approval: re-verify providers, prices and ZDR; choose the provider allow-list | Budget caps active; eval per provider; cost report incl. OpenRouter vs DeepSeek-direct off-peak comparison |

Dependency notes:
- The design tokens (from xype.io) are needed first in Phase 4 (webviews), not Phase 8. Fetch and extract them at the start of Phase 4; if the site cannot be fetched, stop and ask.
- `xype-ai` is developed in the Extension Development Host of stock Code-OSS / VS Code until Phase 8.

## 3. Key design choices made in Phase 0

1. **Queues**: RabbitMQ 4.4 quorum queues instead of classic queues + `x-max-priority` + the delayed-message plugin (ADR-0004, accepted; deviates from the brief's wording).
2. **Embedder**: Qwen3-Embedding-0.6B, **1024 dims** (Q10), served by Ollama locally and TEI when hosted (ADR-0002).
3. **FIM model**: `qwen2.5-coder:1.5b` base on Ollama; 7B optional (ADR-0003).
4. **Paid LLM access**: OpenRouter through one OpenAI-compatible adapter with ZDR, a provider allow-list and sticky sessions; direct DeepSeek is the same adapter with another base URL (ADR-0005, accepted). Config: `LLM_PRIMARY=openrouter:deepseek/deepseek-v4-pro` replaces the brief's `LLM_PRIMARY=deepseek`.
5. **Task states**: `agent_tasks.status` and proto `TaskStatus` add `DEFERRED` (waiting for an off-peak window, `CostScheduler`) and `WAITING_FOR_CLIENT` (client offline or approval pending) to the brief's `QUEUED → STARTED → IN_PROGRESS → FINISHED / FAILED / CANCELLED`. Approved (Q15).
6. **Currency**: all prices, costs and budgets in USD (DeepSeek bills in USD); `budget_usd_month` replaces the brief's `budget_eur_month` (Q14).
7. **Drafts location**: the proto and schema drafts lived in `docs/drafts/` until Phase 1 slice 5 moved them into `proto/xype/v1/` and Alembic, so `buf breaking` starts from a reviewed baseline.

## 3a. Phase 1 follow-ups found during slices 3–4

- TLS with a local dev CA (brief §3.7) for the edge proxy, gRPC, asyncpg (write and read engines, migrations), Redis and AMQP, before the first `/api/v1` or gRPC payload route (Phase 2).
- Encryption at rest (brief §3.7): encrypted volumes for Postgres, Redis and RabbitMQ in the hosted environment, and encrypted `chat_messages` content (Phase 4), before hosting.
- Docker secrets (`*_FILE` variables) for database, Redis and broker passwords instead of environment variables, before hosting.
- Validate JSONB columns (`agent_tasks.parameters`, `events.payload_meta`) against typed models with allow-listed keys at the DAL boundary (with their first repositories).
- Done in slice 7 (ADR-0012): network guard in the test suite, paid-configuration check, integration tests, dependency scan and SBOM in CI.
- Run the embedder integration test (`models` marker) in CI once a cached model volume is affordable there.
- Review accepted vulnerabilities in `osv-scanner.toml` before they expire (first: 2027-01-31).
- Strip userinfo from `repositories.remote_url` at the registration API, with a test (Phase 2).

## 4. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Completion P99 < 200 ms unreachable on the CPU-only dev machine with a 1.5B model, especially if real request rates are ~10× the assumed rate (scale-estimates §2.1) | High | Measure early in Phase 3 and report honestly; client cache + cancellation; small context window; GPU/vLLM in the hosted environment |
| R2 | Code-OSS fork build: heavy toolchain (VS 2026, Spectre libs, Rust), long builds, upstream churn; 8 GB RAM is the build's own recommendation, so it cannot run next to the stack locally | High | Postpone to Phase 8; build on GitHub Actions runners only; keep patches minimal; VSCodium scripts as reference |
| R12 | 8 GB RAM total on the dev machine (Q5) | High | Compose stack ≤ 4 GB (scale-estimates §6): one Ollama runtime for FIM + embeddings, observability behind a compose profile, testcontainers run one service at a time |
| R3 | Strict priority in quorum queues can starve agent tasks under steady chat load | Medium | Separate `chat` and `agent` queues with independent consumers, or bounded prefetch; documented in ADR-0004 |
| R4 | Prebuilt tree-sitter WASM grammars for SQL, YAML, Dockerfile may be missing or ABI-incompatible | Medium | Confirmed by the Phase 2 spike (no prebuilt SQL, Markdown or Dockerfile `.wasm`): all grammars are built from pinned sources with tree-sitter-cli 0.27.1 and load with ABI 14–15 (ADR-0017); line-window fallback for files that fail to parse |
| R5 | Server asks the client for chunks over the stream; if the client is offline, chat and agents lose context | Medium | Clear degraded mode; short-lived encrypted client cache; agent tasks pause with `WAITING_FOR_CLIENT` progress event |
| R6 | Model-inversion attacks on stored embeddings can partially reconstruct code | Medium | Per-tenant key rotation, scoped access, no plaintext anywhere; documented in `privacy.md` (Phase 2) |
| R7 | CPU embedding throughput too low for the initial 300k-chunk index (~4 h estimate) | Low/Med | Resumable batch indexing; MRL to 768 dims; GPU host later |
| R9 | Through OpenRouter, DeepSeek V4 is served by third-party hosts: prices above DeepSeek's own off-peak rate, no off-peak discount (weakens `CostScheduler` savings), possibly different checkpoints/quantizations, data-residency differences per host | Medium | ZDR on every request; provider allow-list reviewed in Phase 10; per-provider eval; keep the `deepseek` direct profile as a cost fallback (ADR-0005) |
| R10 | Accidental paid API call during development | High (policy) | Network guard in tests, PreToolUse hook, CI check that fails on paid provider config |
| R11 | Company OS is REST, but the API spec is not shared yet; template repos are not ready | Medium | Fakes with fixtures and seeded sample repos until available (Q1a, Q2) |

## 5. Assumptions

- Local development on Windows 11 with Docker Desktop, CPU only, 8 GB RAM (Q5).
- One tenant (XYPE) but `team_id` and `project_id` exist from day one for cache keys and budgets.
- English for code, docs, commits; the agent chats with the user in Bulgarian.
- `master` is the default branch (Q3). GitHub account `yordan-gergov01` (Q6).
- Company OS is reached over REST (Q1); template repos come later (Q2).
