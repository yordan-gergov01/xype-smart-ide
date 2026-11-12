# XYPE server

Backend (gateway), workers and the shared library, in the layout of XYPE's template_core
(ADR-0006). The LLM worker has its provider seam; its process and the indexer arrive with
later phases in `docs/plan.md`.

| Path | Responsibility |
|---|---|
| `backend/` | The API: `main.py` (composition root), `api/` (routes, middleware), `services/`, `dal/`, `messaging/`, `core/` (settings, clients); `Dockerfile` |
| `llm/` | LLM worker: `providers/` with the `LLMProvider` Protocol and `MockLLMProvider`; no database or cache access |
| `common_lib/` | Code every service imports: errors, domain types, settings, logging, tracing, correlation ids, clock, `SecretBytes`, LLM value types, ORM models (`models/`, backend and migrations only), the embedder (`embeddings/`, with its HTTP client), security (`security/`: payload encryption, key handling, obfuscated ids, scrubbing; ADR-0015, ADR-0016, ADR-0019) and the Redis key store (`keystore/`, Redis only) |
| `tests/unit/<package>/` | Unit tests, no I/O |
| `tests/integration/` | Real Postgres, Redis, RabbitMQ and Ollama through testcontainers (Docker needed). The embedder test uses the models in the `xype_ollama_data` volume: start `deploy/docker-compose.yml` once to download them |
| `tests/support/` | Fakes and contract suites shared by unit and integration tests (importable as `support`) |
| `tests/architecture/` | Runs the import-linter contracts from `pyproject.toml` |
| `migrations/` | Alembic revisions generated from `common_lib.models` and reviewed by hand (ADR-0035) |
| `requirements/` | `.in` files with direct dependencies, compiled `.txt` files with hashes (ADR-0007) |

## Local development setup

Python 3.12. From `server/`, on Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

On Linux or macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Then install the pinned tools and make the packages importable:

```bash
python -m pip install --require-hashes -r requirements/dev.txt
python -m pip install -e . --no-deps
```

Generate the protobuf modules into `server/gen/` (not committed; mypy and the tests import
them; pre-commit regenerates them when `proto/` changes):

```bash
python -m devtools.gen_proto
```

Create the local dev CA once from the repository root for the compose stack (ADR-0036;
Git ignores `deploy/certs/`; the integration tests issue their own throwaway CA):

```bash
python tools/server_tool.py python -m devtools.dev_ca
```

Install the Git hooks once from the repository root with `pre-commit install`. The mypy and
import-linter hooks run from `server/.venv` through `tools/server_tool.py`, so the venv does
not have to be active.

## Running the checks

With the venv active, from `server/`:

```bash
ruff format --check .
ruff check .
mypy
lint-imports
pytest --cov
```

From the repository root without activating it: `python tools/server_tool.py <tool> [args]`.
Unit tests run by default; `pytest -m integration` runs the testcontainers tests (they pull
the images pinned in `deploy/docker-compose.yml`). CI runs `-m "integration and not models"`:
tests marked `models` need the model volume compose downloads. A network guard refuses every
connection from the test process to another machine (`tests/support/network_guard.py`, ADR-0012).
Coverage must stay at 85% or more.

## Adding or upgrading a dependency

Add the direct dependency to the right `.in` file with a one-line reason, then recompile.
Service files come first, `dev.in` last, because it is constrained by them:

```bash
uv pip compile --universal --generate-hashes --python-version 3.12 requirements/dev.in -o requirements/dev.txt
```

`uv` is installed in the venv from `dev.txt`. To upgrade one package add
`--upgrade-package <name>`. Reinstall with `pip install --require-hashes -r requirements/dev.txt`,
run the checks, and commit the `.in` and `.txt` files together. CI recompiles and fails if the
`.txt` files are out of date. If it reports drift without an `.in` change (new wheels for a
pinned version change the hash list), recompile and commit the result.

## Changing the database schema

Change a model in `common_lib/models`, then generate a revision against an empty
database (for example a throwaway `pgvector` container with `CREATE EXTENSION vector;
CREATE SCHEMA migrations;`, or one that mounts `deploy/docker/postgres/initdb/`)
and review it by hand before committing:

```bash
alembic revision --autogenerate -m "<short description>"
```

`alembic` reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` (the schema
owner role), and `TLS_CA_FILE` (the dev CA's `ca.crt`): it connects over TLS
only. `pytest -m integration` then checks that the migrations build exactly the
models' schema. In compose, the `migrate` service applies them before the backend starts.
