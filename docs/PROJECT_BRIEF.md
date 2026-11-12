# XYPE IDE — Project Brief and Build Instructions for Claude Code

> Put this file in the repo root (e.g. `docs/PROJECT_BRIEF.md`) and start the session with:
> "Read docs/PROJECT_BRIEF.md fully and start with Phase 0."

---

## 0. Who you are and what we are building

You are a principal engineer building **XYPE IDE**, an AI-native IDE. It is a **fork of Code-OSS** with its own design. A built-in AI layer understands **only the registered codebases, framework templates, project context and team conventions**.

The architecture must follow **Chapter 3, "Case Study: Designing AI-Native IDEs"**, of *System Design for the LLM Era* (Sampriti Mitra, Packt). Every architectural decision from that chapter is listed in section 3 below. Implement each one, or record an ADR explaining a justified deviation.

**Product goal.** It must be simpler and easier to use than a plain code editor, and more professional. A developer opens XYPE IDE and can do the following in a few clicks:

- pick a repo or a Jira ticket;
- chat with the codebase;
- get completions;
- run background agent tasks;
- start a new project from one of the registered framework templates.

**Users and scale.** 5–6 developers, 10–15 repositories. Design and load-test for **10× headroom**: 60 users and 30 repositories.

**Cost rule. This is critical.** Development must be **completely free**:
- No paid LLM or embedding API is called at any point during development, tests, CI or evals.
- Embeddings are always produced by a free, local, open-source model.
- LLM calls go through a **mock provider** by default, or through a free local model.
- A **DeepSeek V4 Pro** adapter is built and contract-tested against recorded fixtures, but stays disabled. It is activated only at the end by configuration (`LLM_PRIMARY=deepseek` plus an API key), and only after I explicitly approve.

---

## 1. Tech stack (agreed, do not change without an ADR and my approval)

| Area | Choice |
|---|---|
| Backend language | Python 3.12+, fully typed (`mypy --strict`) |
| REST API | FastAPI (auth, admin, health, metrics, webhooks), versioned under `/api/v1` |
| Streaming API | gRPC over HTTP/2 with `grpc.aio`, **bidirectional persistent stream** between client and server (as in the book), contracts in `proto/xype/v1`, managed with `buf` (lint and breaking-change checks) |
| Database | PostgreSQL 16 + **pgvector** (HNSW index), SQLAlchemy 2 (async), Alembic migrations |
| Cache | Redis, using the cache-aside pattern |
| Queue | **RabbitMQ** (priority queues, dead-letter queues, delayed retries, publisher confirms) |
| Embeddings | Free, local, open-source code-embedding model, served from its own container and runnable on CPU. Choose a current, permissively licensed model, record the choice in an ADR, and keep the dimension configurable. |
| Local LLMs (free) | Ollama (dev) and/or vLLM (later), with a small open-weight coder model that supports FIM for code completion and a small chat model for local end-to-end testing |
| Paid LLM (end only) | DeepSeek V4 Pro (`deepseek-v4-pro`) as primary; DeepSeek V4 Flash as backup. DeepSeek offers an Anthropic-compatible endpoint (`https://api.deepseek.com/anthropic`) and an OpenAI-compatible one. Verify the current docs and prices before implementing. |
| IDE | Fork of **Code-OSS**. Use VSCodium's build scripts and approach as a reference: no Microsoft telemetry, and Open VSX as the extension marketplace, because the MS Marketplace may not be used by forks. |
| AI client | Built-in TypeScript extension `xype-ai`, bundled in the fork, containing the client context engine |
| AI UI | React + TypeScript + Vite webviews inside the IDE (chat, tasks, tickets, templates) |
| Admin dashboard | React + TypeScript + Vite web app (usage, cost, tasks, eval results, models, users) |
| Observability | `structlog` JSON logs, OpenTelemetry traces, Prometheus metrics, Grafana dashboards |
| Containers | Docker (multi-stage, non-root, pinned versions, healthchecks), docker compose for local |
| Quality | `ruff`, `mypy --strict`, `pytest` (+ `pytest-asyncio`, `testcontainers`); `eslint`, `tsc --strict`, `vitest`, Playwright for UI; pre-commit hooks |
| CI | GitHub Actions: lint, types, tests, `buf breaking`, image build, eval gate (free models only) |
| Fine-tuning (experiment track) | Google Colab (free GPU tier), QLoRA, export to GGUF for Ollama or to safetensors for vLLM |

Deployment target: **local only for now** (docker compose on my machine). Later it will be hosted, so keep everything 12-factor and environment-driven, with no localhost assumptions in code.

---

## 2. Engineering principles (senior practices — apply everywhere)

**Modules with light interfaces.**
- Every module is an independent package with a small public interface defined as a `typing.Protocol` (Python) or an `interface` (TypeScript), plus one or more adapters.
- The core never imports a concrete provider, database, queue or cache.
- Wiring happens in one **composition root** per process (API, gRPC server, worker), using simple constructor injection. No DI framework unless justified by an ADR.

**Core interfaces (minimum):**
`LLMProvider`, `Embedder`, `VectorStore`, `ChunkRepository`, `Cache`, `SemanticCache`, `TaskQueue`, `TaskHandler`, `ModelRouter`, `PromptRegistry`, `CryptoBox`, `SecretScrubber`, `MerkleIndex`, `ConnectorClient` (project context, Jira, templates), `ToolExecutor` (implemented on the client side), `Clock` and `CostScheduler` (peak/off-peak aware), `Metrics`.

**Every external dependency has three things:**
1. a real adapter;
2. a fake or in-memory adapter for unit tests;
3. contract tests that both must pass.

**Configuration.**
- `pydantic-settings`, environment variables only, and a complete `.env.example`.
- Secrets are never committed, and secret scanning runs in pre-commit and CI.

**Reliability basics.**
- Health (`/healthz`) and readiness (`/readyz`) endpoints.
- Graceful shutdown that drains gRPC streams and workers.
- Idempotency keys on all task submissions.
- Correlation and trace IDs propagated across REST, gRPC, RabbitMQ and workers.

**Logging.**
- Structured JSON logs.
- Request and response bodies are **never** logged (the book's no-logging policy), and a test enforces this.
- Log metadata only: IDs, sizes, latencies, model, token counts and cost.

**Metrics** (RED for every service, plus LLM metrics):
- latency per model and task type;
- token usage and cost per user, project and model;
- cache hit rate (exact, semantic, embedding, and provider prefix cache);
- circuit-breaker state;
- queue depth and retries.

**Versioning.**
- SemVer for every component, conventional commits and a generated CHANGELOG.
- API version in the URL and the proto package.
- Alembic for every schema change.
- Prompt templates are **versioned files in code**, not database rows, as the book recommends.
- The model version is part of cache keys.
- Eval baselines are versioned and stored in the repo.

**Documentation.**
- An ADR for every significant decision, in `docs/adr/`, using the MADR format.
- `docs/book-traceability.md` maps every decision from section 3 to the code that implements it.
- `docs/scale-estimates.md`.
- Runbooks.
- A short `CLAUDE.md` with commands, conventions, the definition of done, and "never do" rules.

**Testing pyramid.**
- Unit tests with fakes.
- Integration tests with testcontainers (Postgres+pgvector, Redis, RabbitMQ).
- Contract tests for adapters.
- End-to-end tests (IDE extension ↔ server) using the mock LLM.
- Load tests (Locust or k6) at the 10× target.
- Target ≥ 85% coverage on core modules.

**Security.**
- Least privilege for everything.
- Containers run as non-root.
- Dependency scanning.
- SBOM generation in CI.
- No plaintext code at rest on the server (see 3.7).

**Definition of done** for every task:
- lint, types and tests pass;
- docs and ADRs are updated;
- the traceability doc is updated;
- the change runs in docker compose;
- no paid API calls were made;
- every rule in `.claude/rules/` is respected (see section 11);
- the commit was confirmed by me.

---

## 3. Architecture: every decision from Chapter 3 (must be implemented)

### 3.1 Requirements

**Functional:**
- codebase context awareness across all files;
- code completion, including fill-in-the-middle;
- chat mode scoped to files or to the whole codebase;
- multi-line and multi-file edits shown as diffs, with accept/reject;
- background agent tasks.

**Non-functional:**
- code completion P99 < 200 ms;
- chat P99 < 3 s to first useful output (stream tokens);
- high accuracy;
- reliability and availability, including local caching on the client;
- security and privacy, with zero-retention and embedding-only on the server.

**XYPE additions:**
- Jira ticket workflow;
- project context (the project-context connector);
- framework templates;
- the shared and repo rules packs injected into every prompt.

### 3.2 Scale estimates

Redo the book's estimation method for this project's scale in `docs/scale-estimates.md`. Starting assumptions, which you should verify and refine:

- **Load.** 6 devs, using the book's 30 completion requests per dev per peak hour, gives about 0.05 RPS. With 10× headroom and a 2× safety margin, design for about **1–2 RPS** for completion. Chat is about 5 queries per dev per day (book), and agent tasks are about 20 per dev per day.
- **Index.** 30 repos at up to about 10k chunks each is about 300k chunks. At 768-dim float32 that is about 0.9 GB of vectors plus HNSW overhead, which pgvector handles comfortably. Keep `VectorStore` behind its interface so that a turbopuffer adapter (the book's high-scale choice) can be added later without touching the core.

### 3.3 API design

The book's API, adapted under `/api/v1` and gRPC:

| Book API | XYPE implementation |
|---|---|
| `POST api/login` | Client credentials exchanged for a short-lived access token plus a refresh token. These secure the persistent gRPC connection. Design it so OIDC/SSO can be added later. |
| `POST api/{codebase}/upload` | Initial indexing: **encrypted** code chunks with **obfuscated metadata** (hashed `file_id`, line ranges). Returns indexed chunk IDs. |
| `GET api/{codebase}/merkle-tree` | Returns the server's hashed Merkle tree for delta sync. |
| `POST api/complete/code` | Code completion with cursor context, surrounding lines, git history and user preferences. Implemented on the gRPC stream for latency, with a REST fallback. |
| `POST api/chat/query` | Chat with scope (`files[]` or `full_codebase`), plus `@web` for non-codebase questions (disabled in v1, interface only). |
| `POST api/agent/execute` | Starts an async agent task and returns `task_id`. Progress is pushed over the gRPC stream. |

Also define protobuf messages for the gRPC stream. The server must be able to:

- request specific chunks or files from the client;
- request tool executions;
- push task progress and completion events;
- push Merkle sync requests.

### 3.4 High-level components (separate deployables or modules)

1. **IDE layer (fork)** captures keystrokes, file changes and queries, and renders completions, chat, diffs and task progress. Core IDE features must stay **separate from the AI capabilities**: the fork only does branding, UX simplification and bundling, and all AI logic lives in the `xype-ai` extension. This way upstream Code-OSS updates can be merged cheaply, and the AI layer ships independently.
2. **Client context engine** (inside `xype-ai`):
   - **AST-based semantic chunking** with `web-tree-sitter` (functions, classes, blocks), for Python, TypeScript/TSX, JS, SQL, YAML, Dockerfile and Markdown at minimum.
   - **Call graph analysis.** When function A changes, find its dependents B and proactively inject them into the context, because vector search misses functional dependencies that don't share text.
   - **Merkle tree** of the workspace for delta sync.
   - **Client-side encryption** (AES-256-GCM with per-session keys) and **metadata obfuscation** (hashed filenames and symbols) before anything leaves the client.
   - **PII and secret scrubbing middleware** (regex plus entropy detection). It replaces secrets with `<SECRET_REMOVED>` **before** encryption and sending.
   - **Minimized code transfer.** Send only the context needed for the request, never the whole codebase.
   - **Idle-time execution.** Hashing is debounced (runs only after > 2 s without typing), CPU-capped at 10–15% (worker thread or process), and a full consistency check runs every 10 minutes.
   - Local client cache for short-term completion recall.
3. **API gateway:**
   - authentication and authorization;
   - in-memory decryption;
   - **rate limiting and throttling** (per user, configurable, default 200 requests per minute);
   - request validation;
   - forwarding to the orchestrator.
4. **Orchestrator engine** (the "GenAI service"):
   - builds prompts from templates, guardrails and guidelines;
   - pulls context through retrieval;
   - chooses the model through the **intelligent router**, by task type (completion, chat, refactor, tests, docs, lint), user preferences, budget and peak/off-peak;
   - calls the LLM through resilience wrappers;
   - post-processes the response (JSON-schema validation, syntax check, anomaly flags);
   - handles fallbacks.
5. **Background agents and cron.** Long-running tasks (write tests, refactor, document, implement a Jira ticket) and scheduled jobs (index drift checks, eval runs, off-peak batch tasks). They call the indexing engine or the orchestrator.
6. **Data storage layer.** PostgreSQL holds relational data plus pgvector for embeddings.
7. **Embeddings and vector search engine.** **Stateless** embedding generation: decrypt in memory, embed, overwrite or drop plaintext immediately, then persist only the vector plus the obfuscated metadata.

### 3.5 Flows (implement exactly, with sequence diagrams in `docs/`)

- **Context awareness.** Persistent gRPC bidi stream, then AST chunking on the client, then encrypt and obfuscate, then stateless embedding on the server, then index. On a query, the query is embedded and run through vector search. The server then **asks the client over the open stream** for the exact chunks by obfuscated ID and line range. The client sends them encrypted, and the orchestrator calls the LLM.
- **Merkle sync.** Triggered by file mutations (debounced) and by a 10-minute poll. The client sends its tree hash, and the server compares top-down in O(log n) to find the changed files and classes. The server requests only the delta chunks, re-embeds them, discards the plaintext and updates its root.
- **Code complete.** This path is **sync**. The client collects the snippet, surrounding code and the file's git history, encrypts them and sends them through the gateway. The orchestrator builds an optimized prompt, reads the model from preferences, calls the model and returns the result.
- **Chat.** This path is **async**. It takes a scope, runs vector search, selectively fetches from the client, builds the prompt with guardrails, streams tokens back and persists encrypted messages.
- **Agent tasks.** These are **async**. Push to RabbitMQ; the worker records the task in the `events` table, then the agent loop runs. **Tools that touch code (read, edit, run tests or commands) are executed on the client** through `ToolExecutor` over the gRPC stream, with user approval rules, because the server never holds the codebase. Progress is pushed to the client over the stream.

### 3.6 Data model (from the book, plus the XYPE additions)

Implement these tables with the indexes the book specifies:

- `users`
- `user_preferences`:
  - `model_id`
  - `privacy_mode`
  - `max_tokens`
  - `tokens_used`
  - **add:** `budget_eur_month`, `allow_paid_models`
- `llm_models`:
  - `name`
  - `max_context_length`
  - `cost_per_token`, extended to input-miss, input-hit and output prices, with peak and off-peak variants
  - `best_for_tasks` (JSONB)
  - `updated_at`
  - **add:** `provider`, `model_version`, `enabled`
- `session` (with `idx_user_last_updated`)
- `chat_messages`:
  - `sequence_number` for strict ordering
  - **encrypted** content
  - `idx_session_created`
- `agent_tasks`:
  - status `QUEUED → STARTED → IN_PROGRESS → FINISHED`, plus `FAILED` and `CANCELLED`
  - `idx_usr_status`
- `code_chunks`:
  - `vector_embedding` (HNSW)
  - `encrypted_chunk_hash`
  - `obfuscated_filename`
  - line range
  - timestamps
  - **no plaintext code column**
- **XYPE additions:**
  - `events` (queue events, attempts and backoff, as in the book's task-queuing section)
  - `teams` and `projects`, for the book's `team_id` cache keys
  - `repositories`
  - `merkle_nodes`
  - `eval_runs` and `eval_results`
  - `cost_ledger`
  - `feedback` (ratings and annotations)

Prompt templates live **in code** (versioned files) and not in the database, as the book recommends.

### 3.7 Privacy patterns (all of them)

**Ephemeral guarantee:**
- in-memory decryption;
- no persistent plaintext code or filenames;
- memory hygiene (drop and overwrite plaintext right after embedding);
- the no-logging middleware strips bodies from logs and traces.

**Code cannot be reconstructed:**
- only embeddings plus obfuscated metadata are stored;
- document the model-inversion caveat from the book;
- mitigate with key rotation and strictly scoped access.

**Other patterns:**
- minimized attack surface;
- encryption in transit (TLS, including locally via a dev CA) and at rest;
- client-side PII and secret scrubbing.

Write a test suite that verifies these guarantees, e.g.:
- no plaintext code is in Postgres, Redis, logs or traces after indexing;
- scrubbed secrets never reach the provider adapter.

### 3.8 Latency

- **Cache-aside with Redis**, using the book's key designs:
  - vector-search results: `user_id:team_id:query_hash`
  - boilerplate completions: `team_id:model_id:model_version:file_id:code_snippet_hash`
  - explanations: `team_id:model_id:model_version:query_hash`
  - an **embedding cache** keyed by chunk hash.
- **Normalized cache keys:** trim whitespace, lowercase, and optionally remove stop words (configurable and off for code).
- **Semantic cache** for explanations: cosine similarity > 0.95 serves the cached answer.
- **Invalidation** on source modification (via Merkle deltas) and on model or prompt version changes.
- **Race-to-response.** If the primary model has not answered within 200 ms, fire the backup in parallel. Wait up to 100 ms more for the primary, otherwise return the backup and tell the user it came from a non-optimal model. Make all thresholds configurable.
- **Speculative decoding** (advanced, phase-gated). Supported for the self-hosted completion path via vLLM, with a draft model and a target model. Behind a feature flag, with an ADR.
- Horizontal-scaling readiness: stateless API, gRPC and worker processes, and Postgres read-replica support in the repository layer through a read/write session split.

### 3.9 Reliability

- **Strict timeouts** on every LLM call. Cancel on timeout to free resources.
- **Circuit breaker** per provider and model, based on latency and error-rate thresholds. Route to healthy alternatives.
- **Retry strategies:**
  - *Interactive (completion):* capped backoff (500 ms start, 1 s max) or fail fast to the backup model.
  - *Async (chat and agents):* exponential backoff with jitter (e.g. 1, 2, 4 minutes), with at most 3 retries.
- **Backup model strategy.** Keep the fallback chain in `llm_models` and in router config. Optionally fan out in parallel, take the first response and cancel the rest; this must be budget-aware.
- **Sync/async hybrid:**
  - completion is synchronous;
  - chat and agents go through RabbitMQ workers;
  - **priority weighting** so chat is consumed before agent tasks (RabbitMQ `x-max-priority`);
  - dead-letter queues;
  - delayed retries;
  - idempotent handlers.
- **Async response delivery.** Task-complete events are pushed over the open gRPC stream, and the client shows loading or progress states.
- Rate limiting and throttling at the gateway.
- Implement the book's decision tree for timeouts, circuit breakers and retries, and document it in `docs/reliability.md`.

### 3.10 Accuracy

**Advanced prompt engineering:**
- structured instructions with **JSON-schema outputs** for edits (`explanation`, `code_diff`);
- contextual knowledge (surrounding code, file history, metadata);
- good and bad examples;
- **negative prompting** (e.g. "Do not add DB migrations", "Do not hallucinate API names");
- model-specific prompt variants.

**Dynamic feedback loop:**
- thumbs up/down plus annotations in the UI, stored in `feedback`;
- feedback is used for prompt tuning and eval datasets.

**Post-processing:** anomaly flags and syntax checks.

**Multi-model use:** offer alternative responses for ambiguous requests.

**Compiler-as-a-judge:** compile, parse or type-check generated code. A syntax error scores 0. This is deterministic and needs no LLM judge.

### 3.11 Testing with test data → `server/evals`

Build an eval harness that runs only on free models (mock and local) during development:

- **Datasets** come from **the registered repositories and templates**. Snapshot them, refresh them regularly, and add seeded repos with intentional gaps and edge cases. No real secrets.
- **Code-complete evals:** remove functions and classes, type the signature, and compare the completion for exact match, AST match and test pass.
- **Chat evals:** strip comments and docs, ask for explanations, and compare against the originals.
- **Agentic evals:** real tasks (add docs, write tests, refactor, implement past Jira tickets). Check out the commit before the merged PR, run the agent, and score with the repository's test suite and linters.
- **Mutation testing:** inject logic and syntax bugs and check whether the system finds and fixes them.
- **Negative prompt testing:** risky requests (e.g. "Delete all database migrations") must be flagged, refused or clarified.
- **CI eval gate** with versioned baselines and a regression threshold.
- A report per model and prompt version, shown in the admin dashboard.

### 3.12 Monitoring and user feedback

- Grafana dashboards for:
  - latency SLOs (completion P99 < 200 ms, chat time-to-first-token);
  - error rates;
  - circuit-breaker state;
  - queue depth;
  - cache hit rates;
  - tokens and cost per user, project and model;
  - eval scores over time.
- Engagement metrics: active users and usage frequency per feature.
- Alert rules for SLO breaches and budget thresholds.

---

## 4. XYPE-specific capabilities

1. **Templates as first-class context.** Framework repositories are registered as **templates**:
   - architecture, deploy, Grafana, FastAPI template, React+TS template, async queues, and others (paths: `[FILL IN]`).

   Requirements:
   - They are indexed like any other repo and always available to retrieval.
   - A "New project from template" wizard in the IDE scaffolds a new repo from one or more templates.
   - The agent prefers existing template patterns over inventing new ones. Encode this as a rule in the system prompt.
2. **Rules and conventions.** A repo rules pack (`.xype/rules.md`) plus a shared rules pack, injected as the **stable prefix** of every prompt (see section 5).
3. **Project-context connector.** It reads project and team context from the project-context system (an external project-management system, separate from Jira) through a `ConnectorClient` adapter. The interface is `[FILL IN: REST API / MCP / DB — ask me in Phase 0]`. Until it is available, use a fake adapter with fixture data.
4. **Jira connector:**
   - list "my tickets";
   - open a ticket in the IDE;
   - "Implement this ticket" starts an agent task with the ticket and the linked project context;
   - the agent posts its status back as a comment and transitions the ticket.

   Use a fake adapter in development, and use the official Jira REST API or MCP when enabled.
5. **Scope.** Retrieval and agents work only on registered repositories and templates. There is no general web search in v1.

---

## 5. Cost architecture (free during development, cheapest possible in production)

- **Default config:**
  - `LLM_PRIMARY=mock`;
  - `LLM_COMPLETION=ollama:<small-fim-coder>`;
  - `EMBEDDER=local:<open-source-code-embedding-model>`.

  CI must fail if any paid provider is configured or if a network call to a paid API is attempted (use a network guard in tests).
- **Mock provider.** It is deterministic and scriptable, and it supports streaming, tool calls, JSON outputs, latency and error injection. With it, reliability features such as timeouts, circuit breakers and race-to-response are fully testable for free.
- **DeepSeek adapter**, built now but disabled:
  - uses the Anthropic-compatible or OpenAI-compatible API;
  - is tested with recorded fixtures only.

  Before enabling it, verify the current prices on DeepSeek's site. My notes from October 2026, *to be verified*:

  | | Off-peak | Peak |
  |---|---|---|
  | Input, cache miss | $0.66 per 1M tokens | double |
  | Input, cache hit | $0.022 per 1M tokens | double |
  | Output | $1.98 per 1M tokens | double |

  Peak hours are 01:00–04:00 and 06:00–10:00 UTC on weekdays.
- **Cost levers built into the architecture:**
  1. **Prefix-cache-friendly prompts.** The stable content (shared rules pack, repo rules pack, template summaries, tool definitions) always comes first and stays byte-identical. Volatile content (query, retrieved chunks, cursor context) goes last. Track the provider cache-hit ratio as a metric.
  2. **`CostScheduler`.** Non-urgent agent and batch tasks (index-wide docs, bulk tests, eval runs on paid models) are deferred to off-peak windows. The user can override this with "run now".
  3. **Router budget awareness.** Per-user and per-project monthly budgets live in `user_preferences` and `cost_ledger`. When the budget is near its limit, the router degrades to cheaper models.
  4. **All caches from 3.8,** so the cheapest call is the one we skip.
  5. **Completion stays local.** The local or fine-tuned small model serves completion, because it is free and fastest.
  6. **Hard kill switch:** a monthly hard cap that blocks all paid calls.

---

## 6. IDE fork: design and UX

- **Branding.** Name "XYPE IDE", plus its own icons, splash/welcome screen and `product.json` (Open VSX, telemetry off).
- **Design system.** Fetch https://xype.io and extract the real design tokens from its CSS:
  - colors (backgrounds, surfaces, text, accents, gradients, borders, states);
  - fonts, radius and spacing.

  Put them in a shared `packages/design-tokens` package (JSON plus CSS variables plus TS). From that single source, generate:
  1. the XYPE dark and light color themes for the workbench;
  2. the styles for the React webviews;
  3. the admin-dashboard theme.

  If you cannot fetch the site, **stop and ask me** for screenshots or CSS. Never invent brand colors.
- **Simplified, professional UX:**
  - an opinionated default layout with the AI panel as a first-class view;
  - a trimmed activity bar;
  - a XYPE home with repos, "My Jira tickets", recent chats, running tasks and "New project from template";
  - a command palette with XYPE actions;
  - inline diff accept/reject;
  - clear model and cost indicators, including a notice when a backup model answered;
  - keyboard-first.

  Use the `frontend-design` skill (or equivalent judgment) for distinctive, non-generic UI.
- **Fork maintenance.** Keep the patches minimal and documented in `fork/patches/`, with a script to rebase onto new Code-OSS releases. Document the build requirements (RAM and time).
- **Developer workflow.** Develop `xype-ai` in the Extension Development Host first, which needs no fork build. Build the full fork only in the packaging phases.

---

## 7. Fine-tuning experiment track (`finetune/`)

This track is separate from the product. It must not block anything.

- **Goal:** a small open-weight coder model, fine-tuned on the registered templates and code, that beats its base model on **the project's** completion evals and is cheap or free to serve locally.
- **Colab notebooks:**
  - data preparation (FIM-formatted samples from the registered repositories, deduplicated, secrets scrubbed, a held-out split by repo);
  - QLoRA training on a free Colab GPU;
  - evaluation with the same harness as `server/evals`;
  - export to GGUF for Ollama or to safetensors for vLLM.
- **Reporting.** Report base vs. fine-tuned vs. (later) DeepSeek on each eval category, with honest numbers. If the fine-tuned model does not beat the base model, say so.
- **Licensing.** Check the licence of the base model and of the training data. Never use outputs of a paid provider for training without checking that provider's terms, and ask me first.

---

## 8. Repository layout (target)

```
xype-ide/
├── CLAUDE.md
├── .claude/                       # agent rules, skills, subagents, hooks, settings (see section 11)
├── proto/xype/v1/                 # gRPC + messages, buf.yaml
├── server/
│   ├── pyproject.toml             # uv workspace; each module its own package
│   ├── gateway/                   # auth, decrypt, rate limiting, REST + gRPC ingress
│   ├── orchestrator/              # prompt building, guardrails, post-processing
│   ├── router/                    # task-type/model/budget/off-peak routing
│   ├── llm/                       # LLMProvider + adapters: mock, ollama, vllm, deepseek; resilience wrappers
│   ├── prompts/                   # versioned templates (files)
│   ├── indexing/                  # Merkle sync, stateless embedding pipeline
│   ├── embeddings/                # Embedder + local adapter + fake
│   ├── retrieval/                 # VectorStore (pgvector), search, call-graph injection
│   ├── cache/                     # cache-aside, semantic cache, key normalization
│   ├── tasks/                     # RabbitMQ TaskQueue, workers, priorities, DLQ, CostScheduler
│   ├── agents/                    # agent loop, tool protocol (client-executed tools)
│   ├── connectors/                # project_context, jira, templates (+ fakes)
│   ├── storage/                   # SQLAlchemy models, repositories, Alembic
│   ├── security/                  # CryptoBox, key rotation, scrubbing checks
│   ├── observability/             # logging, OTel, metrics, body-stripping middleware
│   ├── evals/                     # harness, datasets, scorers, baselines
│   └── apps/                      # composition roots: api, grpc, worker, scheduler
├── clients/
│   ├── xype-ai/                   # built-in extension: context engine, gRPC client, ToolExecutor
│   ├── webview-ui/                # React + TS + Vite (chat, tasks, tickets, templates)
│   └── admin-dashboard/           # React + TS + Vite
├── packages/design-tokens/
├── fork/                          # Code-OSS fork build scripts, product.json, patches, branding
├── finetune/                      # Colab notebooks + scripts
├── deploy/
│   ├── docker-compose.yml         # postgres+pgvector, redis, rabbitmq, embedder, ollama, api, grpc, worker, scheduler, prometheus, grafana, otel-collector
│   ├── grafana/ prometheus/ otel/
│   └── env/.env.example
├── tests/e2e/ tests/load/
└── docs/
    ├── adr/ book-traceability.md scale-estimates.md reliability.md privacy.md
    ├── sequence-diagrams/ runbooks/ cost.md
```

Adjust the layout if you find a better one, but record why in an ADR.

---

## 9. Phases (stop at each ⛔ for my review)

**Phase 0: Plan.** Use plan mode.
- Read this brief fully.
- Verify current versions and docs for: Code-OSS/VSCodium build, `grpc.aio`, pgvector, RabbitMQ client (`aio-pika`), Ollama, `web-tree-sitter`, and the DeepSeek API.
- Produce:
  - `docs/plan.md` with milestones and risks;
  - the ADR list;
  - the scale estimates;
  - the full proto draft;
  - the DB schema draft;
  - the interface list with signatures;
  - a list of questions for me, including the project-context interface and the template repo paths.
- ⛔ Stop.

**Phase 1: Foundations.**
- **First:** the `.claude/` folder, `CLAUDE.md`, the git conventions and the enforcement hooks and CI checks from section 11. Everything after this is built under these rules.
- Monorepo and tooling.
- docker compose with all infrastructure.
- Config, logging, OTel and metrics.
- Storage and migrations.
- All interfaces with fakes.
- Mock LLM provider and local embedder.
- CI with the paid-API guard.
- ⛔ Stop.

**Phase 2: Indexing and context.**
- The client context engine in `xype-ai`: AST chunking, call graph, Merkle tree, crypto, scrubbing, idle-time limits.
- The gRPC stream.
- Gateway auth and rate limiting.
- Stateless embedding and pgvector indexing.
- Merkle delta sync.
- Privacy test suite.
- ⛔ Stop.

**Phase 3: Code completion.**
- The sync path with the local FIM model.
- Caches.
- Race-to-response, timeouts, capped retries and the circuit breaker.
- Latency benchmarks against P99 < 200 ms on local hardware, with results in the docs.
- ⛔ Stop.

**Phase 4: Chat.**
- Scope selection, retrieval, selective fetch from the client.
- Streaming, encrypted persistence.
- Semantic cache.
- Feedback UI.
- Webview UI with the design tokens.
- ⛔ Stop.

**Phase 5: Agents and queue.**
- RabbitMQ priorities, DLQ, backoff and the `events` table.
- The agent loop with client-executed tools and approval rules.
- Multi-file diffs.
- `CostScheduler`.
- ⛔ Stop.

**Phase 6: XYPE connectors.**
- Templates registry and the "new project" wizard.
- Rules packs.
- Jira (fake first) and the project-context connector (fake first).
- The "Implement ticket" flow end to end on the mock model.
- ⛔ Stop.

**Phase 7: Evals and observability.**
- The full eval harness on the registered repositories with free models.
- The CI eval gate.
- Grafana dashboards and alerts.
- Admin dashboard.
- Load tests at 10×.
- ⛔ Stop.

**Phase 8: Fork and branding.**
- Code-OSS fork build, `product.json`, themes generated from the design tokens.
- Simplified layout and XYPE home.
- `xype-ai` bundled.
- Installers for my OS.
- ⛔ Stop.

**Phase 9: Fine-tuning experiment.**
- The Colab notebooks and the evaluation report.
- ⛔ Stop.

**Phase 10: Go live with DeepSeek.** Only after my explicit approval.
- Enable the DeepSeek adapter with budget caps.
- Run the eval suite on DeepSeek V4 Pro and Flash.
- Compare against the local models.
- Tune the router.
- Write a cost report.

---

## 10. Working rules for you (Claude Code)

- Work phase by phase. Keep a task list. Commit in small, logical commits (conventional commits).
- Always verify library APIs against current docs. Never rely on memory for versions or config formats.
- **Never call a paid API.** Never put real (non-sample) code, secrets or data into fixtures, evals or the fine-tuning data without scrubbing.
- Keep it simple. Prefer boring, well-known solutions. Every abstraction must earn its place, and interfaces stay small.
- When a decision in this brief conflicts with reality (performance, a library limitation, Code-OSS build constraints), stop and propose an ADR instead of silently deviating.
- At the end of each phase, report:
  - what was built;
  - how to run it (exact commands);
  - test, coverage and benchmark results;
  - the traceability-doc delta;
  - open risks;
  - the questions for me.
- Follow every file in `.claude/rules/` (section 11) at all times. They take priority over your defaults.

---

## 11. Agent operating rules, git workflow and the `.claude/` folder

Build this in **Phase 1, before any product code**. Base it on:
- Anthropic's current Claude Code best practices;
- senior-engineering practice;
- Matt Pocock's skills.

Verify the current Claude Code docs for the exact formats of `CLAUDE.md`, rules and imports, skills, subagents, hooks and `settings.json` permissions. Do not rely on memory.

### 11.1 Structure

```
CLAUDE.md                          # short, high-signal: what XYPE IDE is, commands, module map, links to rules
.claude/
├── settings.json                  # permissions (allow / ask / deny), hooks
├── rules/
│   ├── 00-workflow.md             # explore → plan → implement → verify → summarize → confirm → commit
│   ├── 01-git.md                  # branches, Conventional Commits, commit discipline, PRs, releases
│   ├── 02-code-quality.md         # DRY, reuse-first, YAGNI, no dead code, size limits, naming
│   ├── 03-architecture.md         # module boundaries, Protocol interfaces, composition roots, deep modules
│   ├── 04-python.md               # typing, async, errors, logging, FastAPI/gRPC/SQLAlchemy conventions
│   ├── 05-typescript-react.md     # strict TS, React + Vite conventions, extension/webview rules
│   ├── 06-testing.md              # TDD in vertical slices, test pyramid, fakes vs mocks, what to test
│   ├── 07-security-privacy.md     # the privacy guarantees from 3.7, secrets, scrubbing, no paid APIs
│   ├── 08-observability.md        # log/metric/trace conventions, never log bodies
│   ├── 09-docs-adr.md             # when an ADR is required, traceability doc, glossary
│   └── 10-llm-prompts.md          # prompt templates, versioning, prefix-cache ordering, eval requirement
├── specs/                         # specs produced by the to-spec flow; one file per feature
├── skills/                        # project skills (ours + adopted from mattpocock/skills)
└── agents/                        # subagents: planner, implementer, test-writer, reviewer, security-reviewer
docs/glossary.md                   # domain language (ubiquitous language) used in code, specs and commits
```

Every rule file must be short, concrete and checkable. Write "do X / never do Y" statements with a one-line reason, not essays. `CLAUDE.md` links to the rules and stays short, so it does not bloat the context.

### 11.2 Workflow rules and human in the loop (`00-workflow.md`)

**1. Explore before coding, and reuse first.**
- Read the relevant module and its interface first.
- Search the repo for existing helpers, patterns and templates before writing new code.
- When you reuse something, state what you are reusing.

**2. Plan before non-trivial work.**
- Write a short plan.
- For large or ambiguous tasks, use the grilling approach: ask me **one question at a time** until the plan is clear.
- Then write or update a spec in `.claude/specs/`.

**3. Implement in small vertical slices,** test-first where practical.

**4. Verify.** Run lint, types, the tests for the affected modules, the size and duplication checks, and the relevant evals. Never claim something works without running it.

**5. Summarize and wait. This is mandatory before every commit.** Present:
- the proposed commit message(s);
- the files changed, with a one-line reason each;
- what was reused vs. newly written;
- the test results;
- the risks and follow-ups.

Then **stop and wait for my explicit confirmation.** Never commit, push, merge, rebase, tag or delete branches without my approval.

**6. Enforce it deterministically, not only by instruction.** In `.claude/settings.json`:
- **`ask`:** `git commit`, `git push`, `git merge`, `git rebase`, `git tag`, `git reset`, `git checkout -- …`, `git branch -D`.
- **`deny`:** force-push to `main`, reading or writing `.env*` and secrets, and calling paid LLM endpoints.

**7. Keep context clean.**
- Use subagents for broad searches and for independent review.
- Use a handoff note (`.claude/specs/<feature>/handoff.md`) when a task spans sessions.

### 11.3 Git conventions (`01-git.md`)

**Conventional Commits,** enforced by a `commit-msg` hook (commitlint or the pre-commit equivalent) and again in CI:

- Format: `type(scope): imperative summary` (≤ 72 chars), with an optional body explaining *why*, and the footer `Refs: XYPE-123` when a Jira ticket exists. Breaking changes use `!` plus a `BREAKING CHANGE:` footer.
- **Types:** `feat`, `fix`, `chore`, `test`, `docs`, `refactor`, `perf`, `build`, `ci`, `style`, `revert`.
- **Scopes** are the module names: `gateway`, `orchestrator`, `router`, `llm`, `indexing`, `retrieval`, `cache`, `tasks`, `agents`, `connectors`, `storage`, `security`, `observability`, `evals`, `xype-ai`, `webview-ui`, `admin`, `fork`, `finetune`, `deploy`, `proto`, `design-tokens`, `claude`.

**Commits:**
- **Atomic.** One logical change per commit. Tests go in the same commit as the code they test. No "WIP" or "misc fixes" commits on shared branches.
- Never mix refactoring with behaviour changes in one commit.
- Never commit generated files (except the lockfiles), secrets, large binaries or model weights. Use `.gitignore` and Git LFS where needed.

**Branches:**
- Format: `feat/XYPE-123-short-slug`, `fix/…`, `chore/…`, `test/…`, `docs/…`, `refactor/…`.
- Short-lived and rebased on `main`.
- `main` is protected and always green.

**Pull requests:**
- PR template covering: what, why, how verified, screenshots for UI, risk level, checklist.
- Squash-merge with a Conventional Commit title.

**Releases:**
- release-please (or semantic-release) generates versions per component and the CHANGELOG from commits.

**Git skills in `.claude/skills/`:**
- `commit` (stage logically, write the message, show the summary, wait for confirmation);
- `open-pr`;
- `release`.

### 11.4 Code quality rules (`02-code-quality.md`)

**No unnecessary code:**
- **YAGNI:** implement only what the current spec needs.
- No speculative abstractions, options or "future-proof" layers.
- Delete dead code instead of commenting it out.

**No duplication, reuse instead:**
- Before adding a function, search for an existing one.
- Extract shared logic only when it is genuinely the same concept (rule of three), into the owning module, behind its interface.
- **CI duplication check** with `jscpd` (or similar) across Python and TS, using a low threshold. New duplicates fail the build.

**Size limits, enforced:**
- **No source file over 500 lines.** Enforce it with:
  1. a Claude Code `PostToolUse` hook that warns after edits;
  2. a pre-commit check;
  3. a CI check.

  Exempt only lockfiles, generated code (e.g. protobuf stubs), Alembic auto-generated revisions if needed, and upstream Code-OSS sources in the fork. List the exemptions explicitly in one config file.
- Also enforce ≤ 50 lines per function and a cyclomatic-complexity limit (`ruff` C901 / ESLint `complexity`). If a limit is hit, split by responsibility, not arbitrarily.

**Naming and comments:**
- Use names from `docs/glossary.md`.
- Comments explain *why*, not *what*.

**Errors:**
- Typed domain errors.
- No bare `except` or swallowed errors.
- Fail fast at the boundaries.

**Dependencies:**
- Add a new dependency only with a reason in the summary.
- Prefer the standard library and the existing deps.

### 11.5 Architecture rules (`03-architecture.md`)

- Each module exposes a small public interface. Other modules import **only** that interface, never internals. Enforce this with an import-boundary check (`import-linter` for Python, ESLint boundaries for TS).
- **Deep modules over shallow ones** (Matt Pocock's codebase-design principle). A module must hide real complexity behind a simple interface. If deleting a module would only move its complexity elsewhere, it is shallow: merge it.
- Use precise vocabulary consistently: module, interface, seam, adapter, depth.
- Wiring happens only in the composition roots in `server/apps/`.

### 11.6 Testing rules (`06-testing.md`)

- **TDD in vertical slices:**
  - agree on the seam and the public interface first;
  - write a failing test through the public interface;
  - make it pass;
  - refactor.
- Test behaviour, not implementation details.
- Prefer fakes (in-memory adapters) over mocks. Use mocks only at true external boundaries.
- **Bugs:**
  - first build a reliable pass/fail reproduction (a failing test), and only then hypothesize about the cause (the `diagnosing-bugs` approach);
  - every fixed bug gets a regression test.

### 11.7 Skills (`.claude/skills/`)

**Adopt from Matt Pocock's skills (`mattpocock/skills`).**
- Check the current repo for the up-to-date skill names and the install method; names change between versions. At the time of writing, the install is `npx skills add mattpocock/skills`.
- Copy or vendor the ones we use, adapt them to the project's conventions, and record the source and version in each skill's header.
- Expected set:
  - **Grilling** (`grilling` / `grill-me` / `grill-with-docs`): clarify requirements one question at a time before big tasks.
  - **Specs and tickets** (`to-spec` / `to-prd` and `to-tickets` / `to-issues`): turn an idea or a Jira epic into a spec, then into small vertical-slice tickets.
  - **`tdd`**
  - **`diagnosing-bugs`**
  - **Architecture** (`codebase-design` and `improve-codebase-architecture`): deep modules, seams, finding shallow modules.
  - **`domain-modeling`:** maintains `docs/glossary.md`.
  - **`code-review`**
  - **`handoff`:** a session handoff note.
  - **`triage`**
  - **`writing-great-skills`:** use it when writing our own skills.

**Our own skills:**
- `commit`, `open-pr`, `release`;
- `new-module` (scaffolds a module with interface, fake, adapter, contract tests and README, all under the size limits);
- `adr`;
- `add-prompt-template` (versioned file, plus eval cases, plus prefix-cache ordering);
- `add-eval-case`;
- `book-traceability-check` (verifies every section-3 decision is implemented and linked).

**Skill quality:**
- Every skill has a precise trigger description, short instructions and a checklist at the end.
- Keep each `SKILL.md` well under 500 lines. Put long references in separate files that the skill links to.

### 11.8 Subagents (`.claude/agents/`)

| Subagent | Tools | Focus |
|---|---|---|
| `planner` | Read-only | Plans and specs |
| `implementer` | Read/write | Code within the plan |
| `test-writer` | Read/write | Tests only |
| `reviewer` | Read-only | Reviews the diff against `.claude/rules/`, DRY, size limits and architecture boundaries. Must not be the agent that wrote the code. |
| `security-reviewer` | Read-only | Privacy guarantees from 3.7, secrets, paid-API guard |

Before every commit summary, run `reviewer` on the staged diff and include its findings in the summary.

### 11.9 Hooks (`.claude/settings.json`) and CI parity

**Hooks:**
- **`PostToolUse` on edits:** format, lint the changed file, and check the file-size limit.
- **`PreToolUse` on Bash:** block destructive commands, force pushes and network calls to paid LLM endpoints.
- **Stop or pre-commit:** run the fast checks for the touched modules.

**CI parity:** CI runs the same checks (format, lint, types, tests, size limit, duplication, import boundaries, commitlint, `buf breaking`, secret scan, paid-API guard, eval gate), so nothing depends only on the agent behaving well.
