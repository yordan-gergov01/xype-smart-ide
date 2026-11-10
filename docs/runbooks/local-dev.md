# Local development stack

`deploy/docker-compose.yml` runs the backend behind an edge proxy, PostgreSQL 16 +
pgvector, Redis, RabbitMQ 4.4 and Ollama, plus Prometheus, Grafana and the
OpenTelemetry collector in the `observability` profile. Design: ADR-0034, ADR-0011.

## Prerequisites

- Docker Desktop with Compose v2, running whenever you commit: the gitleaks pre-commit hook runs in a container.
- On Windows (WSL2), give Docker 4–5 GB of memory. In `%UserProfile%\.wslconfig`:

  ```ini
  [wsl2]
  memory=5GB
  ```

  Then run `wsl --shutdown` and restart Docker Desktop. The default stack (infrastructure
  and backend) is limited to 3.5 GB, which leaves about 0.6 GB of the 4 GB budget for the
  workers (scale-estimates §6); a unit test keeps it that way.
- About 6 GB of disk: the Ollama image and the two models (≈ 1.6 GB download on the
  first start).
- For the `xype-ai` grammar tests: `npm run grammars -w xype-ai` once. The first run
  downloads tree-sitter-cli's toolchain (wasi-sdk and binaryen, ≈ 700 MB) into
  `%LOCALAPPDATA%\tree-sitter` (`~/.cache/tree-sitter` on Linux); later runs skip grammars that are up to date (ADR-0017).

## Start

```bash
cp deploy/env/.env.example deploy/env/.env
```

Change every `dev-only-change-me-*` value before any shared use, then:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env up -d
```

With dashboards:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env --profile observability up -d
```

Check health. `ollama-pull` downloads the models with its own short-lived Ollama server
(models already present are skipped, so later starts need no internet) and exits with
code 0; `ollama` then starts without internet, without root and with cloud models
disabled. Prometheus and Grafana sit on the `ui` network, which also has outbound
access; nothing that handles code does. To update the models, start once with
`OLLAMA_REFRESH=1`.

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env ps -a
```

The first `up` builds the backend image from `server/` (add `--build` after server
changes).

| URL | What |
|---|---|
| `http://127.0.0.1:8000/healthz` | Backend liveness (through the edge proxy; port `BACKEND_PORT`) |
| `http://127.0.0.1:3001` | Grafana (observability profile; `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`) |
| `http://127.0.0.1:9090` | Prometheus (observability profile); scrapes the backend's `/metrics` |

The edge proxy serves only `/healthz` and `/api/v1/`. Readiness (200, or 503 naming the
unavailable dependency) and metrics stay inside the stack:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env exec backend python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/readyz').read().decode())"
```

 To export traces, set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` and start with the
observability profile. If port 8000 is taken by another project, set `BACKEND_PORT`.

The backend has no route outside the stack (internal networks only); the edge proxy is
the only container on the network with the published API port. PostgreSQL, Redis,
RabbitMQ, Ollama and the backend publish no ports. Reach them with
`docker compose ... exec`, for example:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## Stop and reset

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env down
```

`down -v` also deletes the volumes (database, broker, models, metrics, Grafana). Database
credentials are applied only when the volume is first created, so after changing a
`POSTGRES_*` value run `down -v`. The same holds for `GRAFANA_ADMIN_*`; to keep the
models, remove only that volume: `docker volume rm xype_grafana_data`.

## Database schema

The one-shot `migrate` service applies the Alembic migrations with the schema owner
role (`POSTGRES_OWNER_*`) and exits with code 0; the backend starts after it and
connects with the runtime role (`POSTGRES_APP_*`), which cannot change the schema.

A database volume created before the owner and runtime roles existed (before Phase 1
slice 5) has no owner role, and `migrate` fails to log in. Recreate it once; this
deletes the local database data only:

```bash
docker volume rm xype_postgres_data
```

## Memory

Measured on 2026-11-07 (Docker Desktop with 3.7 GiB, CPU only) with `docker stats
--no-stream`, after both models answered once and were still loaded:

| Service | Limit | Measured |
|---|---|---|
| ollama (`qwen2.5-coder:1.5b` + `qwen3-embedding:0.6b`, 4096 context) | 2304 MiB | 1.73 GiB |
| rabbitmq | 384 MiB | 41–160 MiB (alarm at 60% of the limit, so not lower) |
| postgres (empty) | 384 MiB | 37–54 MiB |
| redis | 128 MiB | 9 MiB |
| backend (measured 2026-11-08, idle) | 256 MiB | 76 MiB |
| edge (nginx) | 32 MiB | 4 MiB |
| **Default stack** | **3488 MiB** | **≈ 1.9–2.1 GiB** |
| grafana | 512 MiB | 258–314 MiB (thrashed at a 256 MiB limit on first start) |
| prometheus | 256 MiB | 49 MiB |
| otel-collector | 128 MiB | 54 MiB |
| **Observability profile** | **896 MiB** | **≈ 0.36–0.42 GiB** |

Ollama reports 1.2 GB per loaded model including the KV cache it reserves; a larger
`OLLAMA_CONTEXT_LENGTH` raises that. The FIM model produced 7.4 tokens/s here on a first
cold run; Phase 3 measures completion latency properly.

## Troubleshooting

- `ollama-pull` exited non-zero: the model download failed (network) or a model name
  contains `cloud` (refused). Re-run `docker compose ... up -d`; finished layers are kept.
- RabbitMQ or PostgreSQL fails with a permission error on an old volume created before
  the containers ran as non-root: reset it with `docker compose ... down -v`.
