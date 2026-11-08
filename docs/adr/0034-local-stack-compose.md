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
  `models` (internal: Ollama), `telemetry` (internal: collector, Prometheus, later the
  services), `egress` (only the one-shot model download), `ui` (only Prometheus and
  Grafana, to publish their ports). Services join only the internal networks they
  need when they arrive; workers never join `data`. A test fails if any other service
  joins a network with outside access.
- Ollama: `ollama-pull` runs its own short-lived Ollama server on `egress` to download
  the models into the volume, then exits; `ollama` starts after it, serves the volume
  read-only, has no internet and runs as a non-root user. Both set `OLLAMA_NO_CLOUD=1`,
  the pull script skips models already present (so later starts need no internet;
  `OLLAMA_REFRESH=1` re-pulls) and refuses `cloud` models, and `ollama.com/api` is in
  `tools/paid_api_hosts.txt` (Ollama's cloud models are paid remote inference).
- TEI is not in the local stack (ADR-0002); it is for the hosted environment.
- Only Prometheus and Grafana publish ports, on `127.0.0.1`. The backend's API port
  joins in slice 4 on an `edge` network.
- Every container: image pinned by tag and digest, a non-root user (except the one-shot
  `ollama-pull`), read-only root filesystem, `cap_drop: ALL` with no capabilities added,
  `no-new-privileges`, a memory limit and a healthcheck (except the collector, which has
  no shell, and the one-shot pull). Named volumes are seeded with
  the image's ownership, so Postgres and RabbitMQ need no root entrypoint.
- Memory: the infrastructure is limited to 3.2 GB of the 4 GB budget, leaving ~0.6 GB
  for the server's services (slice 4). RabbitMQ stays at 384 MiB: it raises a memory
  alarm at 60% of its limit.
- PostgreSQL: a non-superuser application role (explicit `NO*` attributes) owns the
  database; pgvector is created by the superuser at init; the password reaches psql
  through `\getenv`, never a command line. Redis: config rendered into a tmpfs
  (password, no persistence, `maxmemory 64mb` LRU); per-service ACL users without
  dangerous commands arrive with the services.
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
- Bad: `ui` is a normal bridge network, so Prometheus and Grafana have outbound access;
  neither handles code. Removing it (no masquerading) is to be tried for hosting.
- Risk: Postgres has 384 MiB and the default 64 MB `/dev/shm`; HNSW index builds in
  Phase 2 may need more (`maintenance_work_mem`, `shm_size`). Revisit then.

### Confirmation

`docker compose ... up -d` reports every service healthy; the compose test passes in CI.

## More information

- Runbook: `docs/runbooks/local-dev.md`.
- template_core `docs/DECISIONS.md` D-001, D-002, D-015.
