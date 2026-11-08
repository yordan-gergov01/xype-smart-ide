# 07 — Security and privacy

## Code privacy guarantees (must hold in every change)
- Server decrypts code only **in memory**; never writes plaintext code, file names, symbols, queries or answers to Postgres, Redis, RabbitMQ, disk, logs or traces.
- Server stores only embeddings + obfuscated metadata (HMAC ids, line ranges). Never add a plaintext code column.
- Plaintext buffers (`SecretBytes`) are wiped right after embedding.
- Client scrubs secrets/PII (regex + entropy → `<SECRET_REMOVED>`) **before** encryption and sending.
- Client sends only the minimum context a request needs, never the whole codebase.
- Embedding-only storage is not a cryptographic guarantee (model inversion): keep key rotation and scoped access.
- The privacy test suite must stay green; add a test when adding a new storage or transport path.

## Secrets
- Never read, print or write real `.env` files or secrets. Use `deploy/env/.env.example` and environment variables.
- Never commit keys, tokens, certificates or credentials. gitleaks runs in pre-commit and CI. The repo is **public**.

## Paid APIs
- Never call a paid LLM or embedding endpoint (DeepSeek, OpenRouter, others) during development, tests, CI or evals. Use the mock provider or local models (Ollama).
- Paid adapters are tested only with recorded fixtures. Enabling them requires the owner's explicit approval (Phase 10).

## Least privilege
- Containers run as non-root, pinned images, healthchecks, minimal capabilities.
- Do give each service only the credentials it needs.
- Do scan dependencies and generate an SBOM in CI.
