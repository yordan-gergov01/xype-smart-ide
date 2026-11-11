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
| [0006](0006-monorepo-layout.md) | Repository layout: XYPE templates for server and web clients | Accepted (deviation, approved) | §8 |

Written in Phase 1:

| # | Title | Status | Brief |
|---|---|---|---|
| [0007](0007-python-dependencies.md) | Python dependencies: pip with compiled, hashed requirements files | Accepted | §1, §8 |
| [0008](0008-npm-typescript-tooling.md) | TypeScript tooling: npm workspaces, TypeScript 6.0, ESLint import rules | Accepted | §1, §8 |
| [0009](0009-no-di-framework.md) | No DI framework: constructor injection in composition roots | Proposed | §2 |
| [0010](0010-configuration.md) | Configuration via pydantic-settings, environment only | Proposed | §1, §2 |
| [0011](0011-observability.md) | Observability: structlog JSON, OpenTelemetry, Prometheus; metadata only | Proposed | §1, §3.12 |
| [0012](0012-paid-api-guard.md) | Paid-API guard: network guard in tests, hooks and CI checks; dependency scan and SBOM | Proposed | §0, §1, §3.7, §5 |
| [0013](0013-grpc-session-stream.md) | Single bidirectional gRPC `Session` stream with oneof envelopes | Proposed | §3.3, §3.5 |
| [0034](0034-local-stack-compose.md) | Local stack: compose networks, least privilege and memory limits | Proposed | §1, §2, §8 |
| [0035](0035-schema-migrations.md) | Schema migrations: Alembic from the models, owner and runtime roles, a migrate job | Proposed | §1, §3.6, §3.8 |
| [0036](0036-tls-local-dev-ca.md) | TLS in transit with a name-constrained local dev CA; TLS-only listeners on every payload hop | Proposed | §3.7 |

Written in Phase 2:

| # | Title | Status | Brief |
|---|---|---|---|
| [0014](0014-auth-tokens-rate-limit.md) | Auth: admin-issued client credentials, short-lived access tokens, rotating refresh tokens, per-user rate limit | Proposed | §3.3, §3.4.3, §3.9 |
| [0015](0015-session-keys-and-decryption.md) | Client-side crypto: AES-256-GCM per-session keys, X25519 key exchange, KEK-wrapped keys, decryption in the indexer | Proposed | §3.4.2, §3.4.3, §3.4.7, §3.7 |
| [0016](0016-metadata-obfuscation.md) | Metadata obfuscation: keyed HMAC-SHA256 per repository for file ids, symbols and chunk hashes | Proposed | §3.3, §3.4.2, §3.7 |
| [0017](0017-ast-chunking-grammars.md) | AST chunking with web-tree-sitter: all grammars built to WebAssembly from pinned sources | Proposed | §1, §3.4.2 |
| [0018](0018-merkle-delta-sync.md) | Merkle tree over the directory structure, level-by-level delta sync | Proposed | §3.3, §3.4.2, §3.5 |
| [0019](0019-secret-scrubbing.md) | Secret and PII scrubbing: path exclusion, then whole-file regex and entropy rules from one shared file, re-checked on the server | Proposed | §3.4.2, §3.7 |
| [0020](0020-vector-store-pgvector.md) | Vector store: pgvector HNSW (cosine) behind `VectorStore`, iterative scans for repository filters | Proposed | §3.2, §3.4.6, §3.4.7 |

Planned (written in the phase that makes the decision):

| # | Title | Phase |
|---|---|---|
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
