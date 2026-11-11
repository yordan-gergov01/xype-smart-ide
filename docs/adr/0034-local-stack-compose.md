# 0034 — Local stack: compose networks, least privilege and memory limits

- Status: Proposed
- Date: 2026-11-07
- Deciders: project owner
- Brief reference: §1 Containers, §2, §8; scale-estimates §6

## Context and problem statement

The local stack runs on an 8 GB, CPU-only machine with about 4 GB for Docker. It holds
code-derived data (embeddings, obfuscated metadata) and later the paid-model adapters,
so who can reach whom matters even locally. template_core already enforces its
communication rules with Docker networks and least-privilege roles.

## Decision drivers

- Same approach as template_core (ADR-0006).
- Least privilege by default; checked by a test, not by review.
- Default stack ≤ 4 GB.

## Considered options

1. **One internal network per relationship, hardened containers, memory limits, a test that checks them.**
2. One default network, hardening by convention.

## Decision outcome

Chosen option: **1**.

- Networks: `data` (internal: Postgres, Redis), `messaging` (internal: RabbitMQ),
  `models` (internal: Ollama), `telemetry` (internal: collector, Prometheus, the
  backend), `api` (internal: edge proxy and backend), `egress` (only the one-shot model
  download), `ui` (only Prometheus and Grafana, to publish their ports), `edge` (only
  the edge proxy, to publish the API port). Services join only the internal networks they
  need when they arrive; workers never join `data`. ADR-0015 adds `keys` (internal: the non-evicting `keystore` Redis, joined only by the backend and the indexer). A test fails if any other service
  joins a network with outside access.
- Ollama: `ollama-pull` runs its own short-lived Ollama server on `egress` to download
  the models into the volume, then exits; `ollama` starts after it, serves the volume
  read-only, has no internet and runs as a non-root user. Both set `OLLAMA_NO_CLOUD=1`,
  the pull script skips models already present (so later starts need no internet;
  `OLLAMA_REFRESH=1` re-pulls) and refuses `cloud` models, and `ollama.com/api` is in
  `tools/paid_api_hosts.txt` (Ollama's cloud models are paid remote inference).
- TEI is not in the local stack (ADR-0002); it is for the hosted environment.
- Only the edge proxy (API), Prometheus and Grafana publish ports, all on `127.0.0.1`.
- The backend handles decrypted code, so it joins internal networks only and has no
  route out. A published port needs a non-internal network, so an nginx edge proxy
  (`nginxinc/nginx-unprivileged`, 32 MiB) is the only service on `edge` and forwards to
  the backend over `api`. It allows only `/healthz` and `/api/v1/` (everything else, including
  `/readyz` and `/metrics`, is 404), has no access log, logs errors only at `crit` (nginx
  error lines repeat the query string), keeps bodies in memory (no temp files) and drops
  client trace headers. Turning off
  masquerading on a bridge network was tried first: Docker Desktop still routes such a
  network to the internet (verified), so it is not a control.
- Every container: image pinned by tag and digest, a non-root user (except the one-shot
  `ollama-pull`), read-only root filesystem, `cap_drop: ALL` with no capabilities added,
  `no-new-privileges`, a memory limit and a healthcheck (except the collector, which has
  no shell, and the one-shot pull). Named volumes are seeded with
  the image's ownership, so Postgres and RabbitMQ need no root entrypoint.
- Memory: infrastructure, backend and edge proxy are limited to 3.5 GB of the 4 GB
  budget, leaving ~0.6 GB for the workers (llm, indexer). RabbitMQ stays at 384 MiB: it raises a memory
  alarm at 60% of its limit.
- PostgreSQL: a non-superuser application role (explicit `NO*` attributes) owns the
  database; pgvector is created by the superuser at init; the password reaches psql
  through `\getenv`, never a command line. Redis: config rendered into a tmpfs
  (password, no persistence, `maxmemory 64mb` LRU); each service has its own ACL user (the
  backend: read, write and connection commands only, keys under `xype:`, `EVALSHA` and
  `SCRIPT LOAD` for the rate limiter, and `PUBLISH` to the one revocation channel
  `xype:auth:revoked`, ADR-0014). It holds no key material:
  session keys live in the separate `keystore` Redis (`noeviction`, network `keys`,
  ADR-0015).
- Backend and edge proxy: `pids_limit`, and `/tmp` tmpfs with `noexec,nosuid,nodev`.
- TLS (ADR-0036): the edge proxy publishes HTTPS only (container port 8443) and
  verifies the backend's certificate; the backend, Postgres, Redis and RabbitMQ have no
  plaintext listener. Each service mounts only its own certificate directory from
  `deploy/certs/` read-only; the CA key is never mounted.
- Grafana: telemetry, update checks, sign-up, anonymous access, external snapshots,
  Gravatar and the plugin admin are off.
- RabbitMQ: vhost `xype` with quorum queues by default (ADR-0004), no guest user;
  service users and permissions arrive with the topology.
- PostgreSQL 16 as in the brief (template_core uses 18).
- `tests/unit/deploy/test_compose.py` checks pinning, hardening, non-root users, no added
  capabilities, loopback-only ports, which services may reach outside networks,
  `OLLAMA_NO_CLOUD`, the collector's `basic` verbosity, the memory budget and that
  `.env.example` and the compose file name the same variables.

### Consequences

- Good: nothing outside the stack reaches the stores, broker or models; one test guards
  the rules.
- Bad: `ollama-pull` runs as root (the model volume is root-owned) with internet
  access, but only for the download and with no capabilities.
- Bad: the OTel collector image has no shell, so it has no container healthcheck;
  Prometheus scrapes it instead.
- Bad: debugging the stores from the host goes through `docker compose exec`.
- Bad: one more container (the edge proxy) in the request path; it will also carry
  gRPC (`grpc_pass`) when the `Session` stream arrives.
- Bad: `ui` is a normal bridge network, so Prometheus and Grafana have outbound access;
  neither handles code. Removing it (no masquerading) is to be tried for hosting.
- Risk: Postgres has 384 MiB and the default 64 MB `/dev/shm`; HNSW index builds in
  Phase 2 may need more (`maintenance_work_mem`, `shm_size`). Revisit then.

### Confirmation

`docker compose ... up -d` reports every service healthy; the compose test passes in CI.

## More information

- Runbook: `docs/runbooks/local-dev.md`.
- template_core `docs/DECISIONS.md` D-001, D-002, D-015.
