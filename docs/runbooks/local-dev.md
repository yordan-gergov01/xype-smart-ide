# Local development stack

The infrastructure in `deploy/docker-compose.yml`: PostgreSQL 16 + pgvector, Redis,
RabbitMQ 4.4 and Ollama, plus Prometheus, Grafana and the OpenTelemetry collector in
the `observability` profile. The server's own services join in Phase 1 slice 4.
Design: ADR-0034.

## Prerequisites

- Docker Desktop with Compose v2.
- On Windows (WSL2), give Docker 4–5 GB of memory. In `%UserProfile%\.wslconfig`:

  ```ini
  [wsl2]
  memory=5GB
  ```

  Then run `wsl --shutdown` and restart Docker Desktop. The infrastructure is limited to
  3.2 GB, which leaves about 0.6 GB of the 4 GB budget for the server's own services
  (scale-estimates §6); a unit test keeps it that way.
- About 6 GB of disk: the Ollama image and the two models (≈ 1.6 GB download on the
  first start).

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

| URL (observability profile) | What |
|---|---|
| `http://127.0.0.1:3001` | Grafana (`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`) |
| `http://127.0.0.1:9090` | Prometheus |

PostgreSQL, Redis, RabbitMQ and Ollama publish no ports. Reach them with
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

## Memory

Measured on 2026-11-07 (Docker Desktop with 3.7 GiB, CPU only) with `docker stats
--no-stream`, after both models answered once and were still loaded:

| Service | Limit | Measured |
|---|---|---|
| ollama (`qwen2.5-coder:1.5b` + `qwen3-embedding:0.6b`, 4096 context) | 2304 MiB | 1.73 GiB |
| rabbitmq | 384 MiB | 41–160 MiB (alarm at 60% of the limit, so not lower) |
| postgres (empty) | 384 MiB | 37–54 MiB |
| redis | 128 MiB | 9 MiB |
| **Default stack** | **3200 MiB** | **≈ 1.8–2.0 GiB** |
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
