# Spec: Phase 1 slice 4 — configuration, observability, minimal backend

Plan: `docs/plan.md` (Phase 1). Decisions: ADR-0009, ADR-0010, ADR-0011, ADR-0034.

## Goal

A backend process that starts in compose, reports its own health and the health of its
dependencies, and makes every request traceable without ever logging or tracing content.

## In scope

- `common_lib`: settings base (environment only, value-free errors), structured logging
  with redaction and message-free exceptions, correlation ids, tracer provider.
- `backend` in the template_core layout: composition root, `/healthz`, `/readyz`,
  `/metrics`, request middleware (correlation id, server span, RED metrics, access
  line), generic 500 handler, readiness probes for Postgres, Redis and RabbitMQ.
- Dockerfile (non-root, pinned base, test stage), compose service on internal networks
  only, edge proxy as the only published port, per-service Redis and RabbitMQ users.

## Out of scope

`/api/v1` routes, gRPC, authentication, TLS (Phase 2), migrations and models (slice 5),
the test network guard (slice 7), workers.

## Acceptance

1. `docker compose up` brings the backend up healthy; `/readyz` is 200 with every
   dependency `ok`, and 503 naming the dependency when one is stopped.
2. No request body, query, path, credential header or exception message appears in logs,
   span names, attributes, events or status, or in a response
   (`tests/unit/backend/test_privacy.py`).
3. Metric labels come from fixed sets: known methods (`_OTHER` otherwise), route
   templates (`<unmatched>` otherwise), status codes.
4. The real probes and the fake meet one contract (`tests/support/probe_contract.py`;
   real ones in `tests/integration/test_probes.py`).
5. The backend has no route out of the stack; the edge proxy serves only `/healthz` and
   `/api/v1/`.
6. Layer contracts hold (`lint-imports`), coverage ≥ 85%, mypy strict, ruff clean.
