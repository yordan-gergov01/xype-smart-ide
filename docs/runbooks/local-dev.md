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

Create the dev CA and the services' certificates (ADR-0036). It writes into
`deploy/certs/`, which Git ignores:

```bash
python tools/server_tool.py python -m devtools.dev_ca
```

Every payload hop is TLS only, and the stack does not start without these files.

If you created the CA before Phase 2 slice 6a, run it once with `--rotate-ca`. An older CA
either lacks `keystore` in its name constraints or has the IP constraint gRPC cannot
verify (ADR-0036); the generator says which.

Generate the keys once (auth: ADR-0014, KEK: ADR-0015) and paste each value into
`deploy/env/.env`. The stack refuses to start while `AUTH_SIGNING_KEY_CURRENT`,
`AUTH_PEPPER_CURRENT` or `KEK_CURRENT` is empty:

```bash
python tools/server_tool.py python -m backend.admin_cli new-key signing-1
```

```bash
python tools/server_tool.py python -m backend.admin_cli new-key pepper-1
```

```bash
python tools/server_tool.py python -m backend.admin_cli new-key kek-1
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
| `https://localhost:8000/healthz` | Backend liveness (through the edge proxy, HTTPS only; port `BACKEND_PORT`) |
| `http://127.0.0.1:3001` | Grafana (observability profile; `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`) |
| `http://127.0.0.1:9090` | Prometheus (observability profile); scrapes the backend's `/metrics` |

The edge proxy serves only `/healthz` and `/api/v1/`. Readiness (200, or 503 naming the
unavailable dependency) and metrics stay inside the stack:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env exec backend python -c "import ssl, urllib.request as u; print(u.urlopen('https://127.0.0.1:8000/readyz', context=ssl.create_default_context(cafile='/certs/ca.crt')).read().decode())"
```

 To export traces, set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` and start with the
observability profile. If port 8000 is taken by another project, set `BACKEND_PORT`.

The backend has no route outside the stack (internal networks only); the edge proxy is
the only container on the network with the published API port. PostgreSQL, Redis,
RabbitMQ, Ollama and the backend publish no ports. Reach them with
`docker compose ... exec`, for example:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env exec postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## Client credentials

An admin creates one client per developer (ADR-0014). The secret is printed once; hand
it over a private channel. The developer enters it in the extension, which keeps it in
the OS keychain.

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env exec backend python -m backend.admin_cli create-team "XYPE Web"
```

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env exec backend python -m backend.admin_cli create-client --team "XYPE Web" --email dev@example.com --name "Dev One"
```

`disable-client <client_id>` stops logins and refreshes and tells every replica, and
`rotate-secret <client_id>` issues a new secret. Both revoke the user's refresh tokens;
access tokens already issued expire within 15 minutes.

| Rotate | How |
|---|---|
| Signing key | Move the current value to `AUTH_SIGNING_KEY_PREVIOUS`, set a new `AUTH_SIGNING_KEY_CURRENT`, restart. Remove the previous key after 15 minutes. |
| Pepper | Move the current value to `AUTH_PEPPER_PREVIOUS`, set a new `AUTH_PEPPER_CURRENT`, restart. Secrets and refresh tokens hashed under the previous pepper keep working. Removing it forces those clients to log in again with a rotated secret. |

## TLS and the dev CA

`devtools.dev_ca` creates one CA (`deploy/certs/ca.crt`, key `ca.key`) and one
certificate per TLS server: `edge`, `backend`, `postgres`, `redis`, `keystore`, `rabbitmq`. Each
service mounts only its own directory at `/certs`, and the CA key is never mounted. The
CA's name constraints let it issue only for these names and `localhost` (IP names are not
constrained, because grpcio's TLS refuses an IP constraint; ADR-0036).
On Windows, file modes do not protect the keys: keep the checkout under your user profile,
or restrict the folder with `icacls deploy\certs /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"`.

| What | How |
|---|---|
| Call the API from the host | `curl --cacert deploy/certs/ca.crt https://localhost:8000/healthz`. On Windows also pass `--ssl-no-revoke`, because the dev CA has no revocation list. |
| Let the IDE trust the CA (slice 11) | Point the extension at `deploy/certs/ca.crt`. Do not import it into the OS store; the client trusts it for XYPE connections only. |
| Renew certificates | Leaves are valid for 90 days. Re-run the generator: it renews leaves with fewer than 30 days left and keeps current ones. Then restart the stack (`docker compose ... up -d --force-recreate`). |
| Replace the CA | Run with `--rotate-ca`, for example after a leaked key or 2 years on. It issues every leaf again; restart the stack and re-trust `ca.crt` wherever you added it. |

A plaintext client is refused by every store: Postgres rejects non-TLS connections in
`pg_hba.conf`, and Redis and RabbitMQ have no plaintext listener.

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
| redis | 96 MiB | 9 MiB |
| keystore (measured 2026-11-11, 8 keys of one user) | 40 MiB | 6 MiB |
| backend (measured 2026-11-08, idle) | 256 MiB | 76 MiB |
| edge (nginx) | 32 MiB | 4 MiB |
| **Default stack** | **3496 MiB** | **≈ 1.9–2.1 GiB** |
| grafana | 512 MiB | 258–314 MiB (thrashed at a 256 MiB limit on first start) |
| prometheus | 256 MiB | 49 MiB |
| otel-collector | 128 MiB | 54 MiB |
| **Observability profile** | **896 MiB** | **≈ 0.36–0.42 GiB** |

Ollama reports 1.2 GB per loaded model including the KV cache it reserves; a larger
`OLLAMA_CONTEXT_LENGTH` raises that. The FIM model produced 7.4 tokens/s here on a first
cold run; Phase 3 measures completion latency properly.

## Troubleshooting

- A service exits with `/certs/tls.crt is missing`, or nginx or uvicorn cannot load a
  certificate: the dev CA was not created. Run the generator, then `docker compose ...
  up -d`. On Docker Desktop, compose creates an empty directory for a missing bind
  source: `deploy/certs/<service>/`, and even `deploy/certs/ca.crt`. The generator fills
  the service directories and replaces an empty `ca.crt` directory with the file.
- `certificate verify failed` or `SEC_E_UNTRUSTED_ROOT` from a host client: pass
  `deploy/certs/ca.crt` as the CA. After `--rotate-ca`, restart every service so none
  still serves a certificate from the old CA.

- `ollama-pull` exited non-zero: the model download failed (network) or a model name
  contains `cloud` (refused). Re-run `docker compose ... up -d`; finished layers are kept.
- RabbitMQ or PostgreSQL fails with a permission error on an old volume created before
  the containers ran as non-root: reset it with `docker compose ... down -v`.
