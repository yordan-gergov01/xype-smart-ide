# 08 — Observability

## Logs
- Do log structured JSON via `structlog`; event names are `area.action` (`chat.started`, `llm.call.timeout`).
- Do log **metadata only**: ids (request, correlation, trace, user, task), sizes, latencies, model, token counts, cost, outcome.
- Never log request or response bodies, code, prompts, queries, answers, file names, headers with credentials, or exception messages that may contain payloads. The body-stripping middleware and its test enforce this.

## Traces
- Do propagate W3C `traceparent` and `x-correlation-id` across REST, gRPC metadata, RabbitMQ headers and workers.
- Never put payload content in span attributes; use sizes and ids.

## Metrics (Prometheus)
- Do expose RED metrics for every service (rate, errors, duration).
- LLM metrics: latency per model and task type; tokens and cost per model; cache hit rate (exact, semantic, embedding, provider prefix); circuit-breaker state; queue depth and retries.
- Do keep label sets fixed and low-cardinality (`model`, `task_type`, `provider`, `cache`, `outcome`). Never use user, project, request or file ids as labels; per-user/project cost lives in `cost_ledger`.
- Metric names: `xype_<area>_<name>_<unit>` (`xype_llm_request_duration_seconds`).

## Health
- Every service exposes `/healthz` (liveness) and `/readyz` (dependencies ready); containers have healthchecks.
