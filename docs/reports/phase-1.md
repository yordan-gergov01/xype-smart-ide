# Phase 1 report: Foundations

- Date: 2026-11-10
- Scope: brief §9 Phase 1 and `docs/plan.md` Phase 1
- Merged as PRs #1–#6 on `master` (last: `1ceaedd`)

## 1. What was built

| Slice | PR | What |
|---|---|---|
| 1–3 | #1 | `.claude/` (rules, skills, agents, hooks, settings), `CLAUDE.md`, Conventional Commit check, pre-commit, gitleaks, size limits. Monorepo tooling: pip with hashed requirements compiled by uv (ADR-0007), npm workspaces with TypeScript 6 and ESLint 10 (ADR-0008). docker compose with Postgres + pgvector, Redis, RabbitMQ 4.4 (quorum default), Ollama with the two local models, and an observability profile (OTel collector, Prometheus, Grafana) |
| 4 | #2 | Backend in the `template_core` layout (ADR-0006): settings from the environment only (ADR-0010), structlog JSON with redaction, OTel traces, Prometheus RED metrics, metadata-only logs and traces (ADR-0011). `/healthz`, `/readyz` and `/metrics`. nginx edge proxy; the backend has no outbound route (ADR-0034) |
| 5 | #3 | SQLAlchemy models and the first Alembic revision (`vector(1024)`, HNSW cosine). Owner and runtime roles, a one-shot `migrate` service, HMAC and no-credentials CHECKs (ADR-0035). gRPC contracts in `proto/xype/v1` (7 files), `buf lint` and `buf breaking` against `master` (ADR-0013) |
| 6 | #4 | The LLM seam (`LLMProvider`) with a deterministic, scriptable `MockLLMProvider`. The `Embedder` seam with `OllamaEmbedder` (`qwen3-embedding:0.6b`, 1024 dimensions). `SecretBytes` (wiped after use) and `Clock`. Each comes with a fake and a shared contract suite. Other interfaces arrive with the phase that brings their real adapter (`docs/interfaces.md`) |
| 7 | #5 | The paid-API guard (ADR-0012): a network guard for the whole test session, a tracked-configuration check, the shell hook and the source check, all sharing `tools/paid_api_hosts.txt`. CI adds testcontainers integration tests, OSV-Scanner and a CycloneDX SBOM |
| — | #6 | Traceability check: 13 new rows (T48–T60), stale references fixed |

Import boundaries are enforced by 9 import-linter contracts (`server/pyproject.toml`):
- the shared library outside the models has no web, transport, broker, cache, HTTP or ORM code;
- the models know no service, transport, broker or cache;
- the embedder may use only an HTTP client;
- the backend and the LLM worker do not import each other;
- the LLM worker has no database or cache access;
- the backend layers depend downwards only, `api` → `services` → `dal`;
- the API layer never touches stores or the broker directly;
- services never import transports, stores or the broker;
- messaging never imports the API.

## 2. How to run it

Setup is in `server/README.md` and `docs/runbooks/local-dev.md`.

```bash
npm ci && npm run check                    # format, lint, tsc, hook tests, jscpd, buf
pre-commit run --all-files                 # everything fast that CI runs
python tools/server_tool.py pytest --cov   # unit tests with coverage
python tools/server_tool.py pytest -m integration   # testcontainers; Docker needed
python tools/server_tool.py lint-imports
```

Stack: copy `deploy/env/.env.example` to `deploy/env/.env`, change the `dev-only-change-me-*` values, then run `docker compose -f deploy/docker-compose.yml --env-file deploy/env/.env up -d`. Add `--profile observability` for the dashboards.

## 3. Results

| Check | Result |
|---|---|
| Unit tests | 165 passed, 1 skipped on Windows (`sendmsg` does not exist there); coverage 96.72% (threshold 85%) |
| Integration tests (testcontainers) | 9 passed with the network guard on: migrations and roles, probes, the real embedder. CI runs all but the two `models` tests |
| Repo check tests | 8 passed |
| Types, lint, boundaries | mypy strict (Windows and Linux), ruff, ESLint, tsc, 9 import contracts, `buf lint`: all clean |
| Size and duplication | No source file over 500 lines, no function over 50 lines; jscpd clean |
| Dependency scan | OSV-Scanner clean, with one accepted dev-only finding until 2027-01-31 (`osv-scanner.toml`) |
| CI on `master` | 7 jobs green: repo-checks, python, integration, node, dependency-scan, sbom, secret-scan |
| Compose | Default stack healthy. Last live run during slice 5 (2026-11-08); not re-run for this report |
| Memory (default stack) | Limits 3488 MiB, measured ≈ 1.9–2.1 GiB idle. Observability profile: +896 MiB limits, ≈ 0.4 GiB used (`docs/runbooks/local-dev.md`) |
| Network guard proven | `server/tests/architecture/test_network_guard.py`: every paid host and every connect path is refused. `server/tests/conftest.py` makes a swallowed refusal fail the test (checked by hand) |
| Paid API calls | None. Only the mock provider and local Ollama models were used |

No benchmarks yet: completion latency is measured in Phase 3.

## 4. Traceability delta

Out of 60 rows (`docs/traceability.md`):
- 2 implemented: T21 (data model) and T31 (stateless services, read/write session split);
- 6 in progress: T6, T7, T19, T20, T23, T47;
- 1 deviation: T36, quorum queues (ADR-0004);
- 51 planned.

Phase 1 added rows T48–T60, which cover §3.4.2, §3.5, §3.7 and §4.

ADRs:
- Accepted: 0001, 0004, 0005, 0006, 0007, 0008.
- Proposed, waiting for the owner: 0002, 0003, 0009, 0010, 0011, 0012, 0013, 0034, 0035.

## 5. Open risks and follow-ups

| Item | Where |
|---|---|
| R1: completion P99 < 200 ms on the CPU-only machine is unproven | Phase 3 benchmark |
| R12: 8 GB machine; the stack fits in ≈ 2 GiB with both models loaded, but indexing and completion load are not measured yet | Phase 2–3 |
| TLS with a local dev CA, before the first payload route | `docs/plan.md` §3a, Phase 2 |
| Encryption at rest, before hosting | `docs/plan.md` §3a |
| Docker secrets instead of environment passwords, before hosting | `docs/plan.md` §3a |
| Strip userinfo from `repositories.remote_url` at the registration API, with a test | `docs/plan.md` §3a, Phase 2 |
| Validate JSONB columns (`agent_tasks.parameters`, `events.payload_meta`) against typed models with allow-listed keys | `docs/plan.md` §3a, with their first repositories |
| The network guard covers the test process only, not containers, subprocesses or native sockets (a future grpcio) | ADR-0012 |
| The embedder integration test runs on developer machines only | `docs/plan.md` §3a |
| Accepted vulnerability (minimatch, dev-only) expires 2027-01-31 | `osv-scanner.toml` |
| `pre-commit` needs Docker Desktop running (gitleaks hook) | `docs/runbooks/local-dev.md` |

## 6. Questions for the owner

1. Approve the Proposed ADRs: 0002, 0003, 0009, 0010, 0011, 0012, 0013, 0034, 0035.
2. Q11 (blocks Phase 2): are client credentials created per developer by an admin? Is an SSO provider planned (Google Workspace, Entra ID)?
3. Remove the old volume, `docker volume rm xype_postgres_data`; it predates the role split. Optionally also remove the volumes of the temporary projects `xype-s5` and `xype-s5b`.
4. Later phases: Q1a (Company OS API, Phase 6), Q7 (Jira, Phase 6), Q13 (brand assets, Phase 4). All are in `docs/questions.md`.
