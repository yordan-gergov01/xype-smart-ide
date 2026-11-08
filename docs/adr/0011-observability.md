# 0011 — Observability: structlog JSON, OpenTelemetry, Prometheus; metadata only

- Status: Proposed
- Date: 2026-11-08
- Deciders: project owner
- Brief reference: §1 Observability, §3.12; rules 07 and 08

## Context and problem statement

The server handles decrypted code, prompts and answers in memory. Logs, traces and metrics must make every request traceable without ever holding that content.

## Decision drivers

- One request followable across REST, gRPC, RabbitMQ and workers.
- Privacy by construction, proved by tests, not by review.
- Low-cardinality metrics.

## Considered options

1. **structlog JSON + OpenTelemetry SDK with our own middleware + prometheus-client.**
2. OpenTelemetry auto-instrumentation (FastAPI, SQLAlchemy, httpx).

## Decision outcome

Chosen option: **1**.

- **Logs** (`common_lib.logging_config`): structlog JSON to stdout, the same pipeline for library loggers. Each line carries service, level, timestamp, `correlation_id` and `trace_id`. Safety nets:
  - values of sensitive keys (`body`, `code`, `prompt`, `query`, `password`, `token`, …) are redacted at any depth;
  - exceptions are logged as type and frames, never the message;
  - SQLAlchemy, httpx, uvicorn access and similar loggers are raised to WARNING (they log SQL parameters and full URLs below it).
- **Access line** (`backend.api.middleware`): one `http.request` per request with method, route template, status and duration. Never the path, query, headers or body. Probe routes log at DEBUG. Methods outside GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS are reported as `_OTHER`, so clients cannot create label values. A request that fails after its response started is recorded as 500.
- **Correlation**: `x-correlation-id` is reused when valid, otherwise generated, and returned in the response. Only W3C trace context is propagated (no baggage, which would carry client text into later hops); the edge proxy drops `traceparent`, `tracestate` and `baggage` from clients until they are authenticated.
- **Traces** (`common_lib.tracing`): an SDK tracer provider per process, OTLP/HTTP export only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Server spans hold method, route template, status and correlation id only. Spans never record exceptions (`record_exception=False`: the event and status description would carry the message); 5xx responses set status ERROR without a description. Auto-instrumentation is not used: it records URLs and statements we would then have to scrub.
- **Metrics**: prometheus-client, one registry per app, `/metrics` reachable only inside the stack (the edge proxy returns 404). `xype_http_requests_total{method,route,status}` and `xype_http_request_duration_seconds{method,route}`; unmatched routes are `<unmatched>`. The `Metrics` Protocol from `docs/interfaces.md` is added when services emit metrics; transport metrics live in the transport layer.
- **Errors**: an unhandled exception is logged once and answered with a generic 500 `{"code": "INTERNAL_ERROR"}` and the correlation id header.
- **Health**: `/healthz` (process alive) and `/readyz` (each dependency under a timeout; 503 with per-dependency status). Probes are adapters behind the `DependencyProbe` Protocol and translate driver errors into `DependencyUnavailableError` without their messages; any other exception is a bug and propagates. `/readyz` and `/metrics` are served inside the stack only.

### Consequences

- Good: privacy holds even when a developer logs carelessly; one id joins logs, traces and the client's report.
- Bad: no automatic spans for database, Redis or HTTP client calls; added by hand where useful, with the same rules.
- Bad: exception messages are not available in logs; debugging uses the type, frames and a reproduction.

### Confirmation

`tests/unit/backend/test_privacy.py` sends secrets in the body, query, path and an `Authorization` header and raises an exception with a secret message, also after the response started; none of them may appear in logs, span names, attributes, events, status descriptions or the response. `tests/integration/test_probes.py` runs the probe contract against real Postgres, Redis and RabbitMQ. Live check: a query string sent through the edge proxy appears in no container log.
