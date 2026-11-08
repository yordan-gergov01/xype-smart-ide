# 0006 — Repository layout: XYPE templates for server and web clients

- Status: Accepted (layout chosen by the project owner on 2026-11-07; deviates from brief §8)
- Date: 2026-11-07
- Deciders: project owner
- Brief reference: §8

## Context and problem statement

Brief §8 proposes one Python package per server module (`server/<module>/`, about 16 packages) plus composition roots in `server/apps/`. XYPE already has two project templates, `template_core` (Python services) and `template_web` (React + Vite), and the owner wants XYPE IDE to follow their structure so that every XYPE project looks the same. Phase 0 also left proto and schema drafts in `docs/drafts/` before `buf` and Alembic exist.

## Decision drivers

- One structure across XYPE projects: anyone who knows the templates can navigate this repo.
- Layering checked by tools (import-linter, ESLint), not by review.
- Small number of deployable units; no speculative packages.

## Considered options

1. **template_core layout for `server/`, template_web layout for the React clients.**
2. Brief §8 as written: one uv workspace package per module, `server/apps/` composition roots.

## Decision outcome

Chosen option: **1**, because the owner chose it and it keeps XYPE projects uniform.

### Server (`server/`, one Python project)

| Path | Responsibility |
|---|---|
| `backend/` | The only network entry point (gateway): REST `/api/v1`, gRPC `Session` stream, health. Owns the database. |
| `backend/api/v1/<feature>/router.py`, `backend/api/dependencies/` | REST transport only: parse, call a service, map the result |
| `backend/grpc/` | gRPC servicer, same rules as `api/` |
| `backend/services/<feature>/service.py` | Business rules, one package per brief module: orchestrator, router, retrieval, cache, tasks, agents, connectors, prompts, security. Each declares the small `Protocol`s it needs and has a `build_<feature>_service()` |
| `backend/dal/<feature>/repository.py` | Every Postgres (SQLAlchemy) and Redis access; returns plain records |
| `backend/core/`, `backend/messaging/` | Database and broker connections, startup, periodic jobs; publishers and consumers |
| `backend/main.py` | Composition root of the backend |
| `llm/` | LLM worker: consumes jobs from RabbitMQ, calls `llm/providers/` (mock, ollama, openai_compatible), publishes results. No database access |
| `indexer/` | Embedding and indexing worker (added in Phase 2) |
| `common_lib/` | Imported by every service: errors, domain types, settings base, logging, request ids, ORM models, schemas, the message contract |
| `migrations/` | Alembic revisions |
| `prompts/`, `evals/` | Versioned prompt templates (data) and the eval harness |
| `tests/unit/<package>/`, `tests/integration/`, `tests/architecture/` | Tests; the architecture tests run the import-linter contracts |
| `requirements/` | Pinned, hashed dependencies per service (ADR-0007) |

The brief's modules (§2, `docs/interfaces.md`) keep their names and interfaces; they become feature packages instead of distribution packages. Composition roots are `backend/main.py`, `llm/main.py` and `indexer/main.py`; adapters are wired there and in `backend/api/dependencies/services.py`.

Import contracts (import-linter, run by pre-commit, CI and `tests/architecture`), added as the modules appear:

- `backend` and each worker do not import each other.
- `backend.api` and `backend.grpc` → `backend.services` → `backend.dal` (layers); transports never import `dal`, models or the ORM.
- `backend.services` never imports `backend.messaging`, transports, `fastapi`, `grpc` or `aio_pika`.
- Workers have no database access (`common_lib.models`, `sqlalchemy` forbidden).
- `common_lib` outside `common_lib.models` imports no ORM, transport, broker, cache or HTTP client.

### TypeScript (`clients/`, `packages/`)

- npm workspaces (ADR-0008).
- `clients/webview-ui/` and `clients/admin-dashboard/` follow template_web: `src/{features,components/{ui,layout},lib,utils,config,types,providers,pages,routes}`, `@/` alias, ESLint `no-restricted-imports` rules: features never import other features; shared code never imports features, pages or routes.
- `clients/xype-ai/` (VS Code extension) is not a web app; it takes the tooling and the boundary style, with its own folders (context engine, gRPC client, `ToolExecutor`).
- `packages/design-tokens/` stays the single source of brand tokens.

### Drafts

The Phase 0 drafts (`xype_v1.proto`, `schema.sql`) moved to `proto/xype/v1/` (ADR-0013) and to `common_lib.models` with the first Alembic revision in `server/migrations/` (ADR-0035) in Phase 1 slice 5, and were deleted (no duplicate sources of truth).

### Consequences

- Good: same structure and checks as the XYPE templates; layering is enforced; fewer packaging files.
- Good: the layering is stricter than the brief's per-package boundaries (api → services → dal).
- Bad: module boundaries inside `backend/services/` are import-linter contracts, not package boundaries; a contract must be added with each feature.
- Bad: `docs/interfaces.md`, rule 03 and the module map changed from the brief.

### Confirmation

`lint-imports` and `tests/architecture/test_import_contracts.py` pass in pre-commit and CI; each new feature package adds its contract in the same commit.

## Pros and cons of the options

### template layout

- Good: uniform with other XYPE projects; worked example of every layer in template_core.
- Bad: deviates from the brief's wording.

### One package per module

- Good: boundaries are packaging boundaries.
- Bad: about 16 `pyproject.toml` files and a workspace to maintain before most modules have code.

## More information

- Templates: `yordan-gergov01/template_core`, `yordan-gergov01/template_web`.
- ADR-0007 (Python dependencies), ADR-0008 (npm and TypeScript).
