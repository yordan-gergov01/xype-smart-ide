# Architecture Decision Records

Format: [MADR](https://adr.github.io/madr/) — copy [`0000-template.md`](0000-template.md). One decision per file, numbered, never renumbered. Superseded ADRs stay in place with a link to the replacement.

## Index

Written in Phase 0:

| # | Title | Status | Brief |
|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions with MADR | Accepted | §2 |
| [0002](0002-embedding-model.md) | Local embedding model: Qwen3-Embedding-0.6B (Ollama locally, TEI hosted) | Proposed | §1, §3.4.7 |
| [0003](0003-fim-completion-model.md) | Local FIM completion model: qwen2.5-coder 1.5B on Ollama | Proposed | §1, §5 |
| [0004](0004-rabbitmq-quorum-queues.md) | RabbitMQ 4.4 quorum queues with native priorities and delayed delivery | Accepted (deviation, approved) | §3.9 |
| [0005](0005-paid-llm-openrouter.md) | Paid LLM access through OpenRouter (OpenAI-compatible adapter) | Accepted | §1, §5 |
| [0006](0006-monorepo-layout.md) | Monorepo layout and Phase 0 drafts location | Proposed | §8 |

Planned (written in the phase that makes the decision):

| # | Title | Phase |
|---|---|---|
| 0007 | Python workspace: uv workspace, one package per module, import-linter contracts | 1 |
| 0008 | TypeScript workspace: pnpm workspaces, ESLint boundaries | 1 |
| 0009 | No DI framework: constructor injection in composition roots | 1 |
| 0010 | Configuration via pydantic-settings, environment only | 1 |
| 0011 | Observability stack: structlog JSON, OpenTelemetry, Prometheus, Grafana; body-stripping policy | 1 |
| 0012 | Paid-API guard: network guard in tests, hook, CI check | 1 |
| 0013 | Single bidirectional gRPC `Session` stream with oneof envelopes | 1–2 |
| 0014 | Auth: client credentials → short-lived access + refresh tokens; OIDC-ready | 2 |
| 0015 | Client-side crypto: AES-256-GCM per-session keys, key exchange and rotation | 2 |
| 0016 | Metadata obfuscation: keyed HMAC for file IDs and symbols | 2 |
| 0017 | AST chunking with web-tree-sitter; grammar build and ABI pinning | 2 |
| 0018 | Merkle tree design and O(log n) delta sync | 2 |
| 0019 | Secret / PII scrubbing: regex + entropy rules | 2 |
| 0020 | Vector store: pgvector HNSW behind `VectorStore` (turbopuffer later) | 2 |
| 0021 | Cache-aside key design, normalisation and invalidation | 3 |
| 0022 | Race-to-response thresholds and backup model | 3 |
| 0023 | Circuit breaker and retry policies (decision tree) | 3 |
| 0024 | Speculative decoding on vLLM behind a feature flag | 3 (gated) |
| 0025 | Semantic cache threshold (cosine > 0.95) and scope | 4 |
| 0026 | Prompt templates as versioned files; prefix-cache ordering | 4 |
| 0027 | Agent tool protocol and approval rules (client-executed tools) | 5 |
| 0028 | `CostScheduler`: peak/off-peak windows and override | 5 |
| 0029 | Connectors: Jira and Company OS adapters | 6 |
| 0030 | Eval gate: baselines, thresholds, compiler-as-a-judge | 7 |
| 0031 | Code-OSS fork strategy (VSCodium-style, Open VSX, patch set) | 8 |
| 0032 | Design tokens package and theme generation | 4 / 8 |
| 0033 | Fine-tuning base model and licence check | 9 |
