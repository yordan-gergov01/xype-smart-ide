---
paths:
  - "server/**/*.py"
  - "tools/**/*.py"
  - "finetune/**/*.py"
---

# 04 — Python

## Typing
- Do type everything; `mypy --strict` must pass. Never use `Any` without a `# reason:` comment.
- Do use `typing.Protocol` for interfaces and frozen `@dataclass(slots=True)` for domain values.
- Do use `from __future__ import annotations` only if needed; target Python 3.12 syntax (`X | None`, `type` aliases).

## Async
- Do use `async` for all I/O. Never call blocking I/O in async code; use `asyncio.to_thread` when unavoidable.
- Do pass explicit timeouts to every network call and cancel on timeout.
- Never create fire-and-forget tasks without keeping a reference and handling their errors.

## Errors
- Do define one base error per module (`XypeError` → `LLMError` → `LLMTimeoutError`).
- Do translate adapter exceptions into domain errors at the adapter boundary.

## Logging
- Do use `structlog` with keyword fields (`log.info("chat.done", model=..., tokens_out=...)`).
- Never log bodies, code, prompts, queries, answers, file names or secrets (see 08-observability).

## FastAPI
- Do version routes under `/api/v1`. Do use Pydantic models for every request/response.
- Do keep route handlers thin: parse → call service via interface → map result.

## gRPC (`grpc.aio`)
- Do implement servicers as thin adapters over services. Do propagate deadlines and metadata (correlation id, traceparent).
- Do drain streams on shutdown (graceful stop with grace period).

## SQLAlchemy 2 (async)
- Do use the 2.0 style (`select()`, `Mapped[...]`, `mapped_column`). Never use the legacy `Query` API.
- Do keep sessions short and scoped per unit of work; use the read session for queries that tolerate replica lag.
- Every schema change is an Alembic revision, reviewed by hand.

## Style
- ruff (lint + format) is the source of truth; line length 100.
