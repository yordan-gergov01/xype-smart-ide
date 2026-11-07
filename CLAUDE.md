# XYPE IDE

AI-native IDE for the XYPE agency: a Code-OSS fork (branding + UX only) plus the built-in `xype-ai` extension, backed by a Python server (FastAPI + gRPC + RabbitMQ workers, Postgres/pgvector, Redis). The AI layer works only on XYPE repos, templates, company context and conventions.

Requirements: [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) · Plan: [docs/plan.md](docs/plan.md) · ADRs: [docs/adr/](docs/adr/README.md) · Interfaces: [docs/interfaces.md](docs/interfaces.md) · Glossary: [docs/glossary.md](docs/glossary.md) · Traceability: [docs/traceability.md](docs/traceability.md)

## Talking to the owner

- Chat with the owner in **Bulgarian**. Code, docs, commits and comments are in **English**.
- Default branch is `master`. Work on short-lived branches (`feat/…`, `fix/…`, `chore/…`, `docs/…`).

## Module map

| Path | What lives there |
|---|---|
| `proto/xype/v1/` | gRPC contracts (buf v2) |
| `server/<module>/` | Python packages `xype_<module>`: gateway, orchestrator, router, llm, prompts, indexing, embeddings, retrieval, cache, tasks, agents, connectors, storage, security, observability, evals |
| `server/apps/` | Composition roots: api, grpc, worker, scheduler — the only place adapters are wired |
| `clients/xype-ai/` | VS Code extension: context engine, gRPC client, `ToolExecutor` |
| `clients/webview-ui/`, `clients/admin-dashboard/` | React + TS + Vite |
| `packages/design-tokens/` | Single source of brand tokens |
| `deploy/` | docker compose, Grafana, Prometheus, OTel, `env/.env.example` |
| `fork/`, `finetune/` | Code-OSS fork build, fine-tuning experiment |
| `tools/` | Repo checks (size limits, guards) |

## Commands

```bash
uv sync --directory server                 # Python deps (Python 3.12)
uv run --directory server ruff check . && uv run --directory server ruff format --check .
uv run --directory server mypy             # strict
uv run --directory server pytest           # unit; add -m integration for testcontainers
pnpm install && pnpm lint && pnpm typecheck && pnpm test
python tools/check_size.py                 # 500-line source limit
pre-commit run --all-files                 # everything CI runs that is fast
docker compose -f deploy/docker-compose.yml up -d
```

Some commands become available as Phase 1 slices land; see `docs/plan.md`. Repo tooling needs Node 24 (`.nvmrc`) and Python ≥ 3.11; the server targets Python 3.12. Paid API endpoints are listed once in `tools/paid_api_hosts.txt` (hook, CI guard and settings stay in sync).

## Rules

All files in `.claude/rules/` apply and override defaults. Start with [00-workflow](.claude/rules/00-workflow.md) and [01-git](.claude/rules/01-git.md).

## Definition of done

- Lint, types and tests pass for the touched modules; coverage ≥ 85% on core modules.
- Docs, ADRs, glossary and `docs/traceability.md` updated.
- Runs in docker compose.
- No paid API call was made (mock or local models only).
- The `reviewer` subagent reviewed the staged diff.
- The owner confirmed the commit.

## Never

- Never commit, push, merge, rebase, tag, reset or delete branches without the owner's explicit yes.
- Never call a paid LLM or embedding API (DeepSeek, OpenRouter, …) outside Phase 10 approval.
- Never read or write real `.env` files or secrets; only `.env.example`.
- Never log request/response bodies, code, queries or answers.
- Never store plaintext code or file names on the server.
- Never add a dependency without stating why in the summary.
- Never exceed 500 lines per source file or 50 lines per function.
- Never mention or cite external books in the repo.
