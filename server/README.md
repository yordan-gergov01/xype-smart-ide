# XYPE server

Backend (gateway), workers and the shared library, in the layout of XYPE's template_core
(ADR-0006). The workers arrive with later slices in `docs/plan.md`.

| Path | Responsibility |
|---|---|
| `backend/` | The API: `main.py` (composition root), `api/` (routes, middleware), `services/`, `dal/`, `messaging/`, `core/` (settings, clients); `Dockerfile` |
| `common_lib/` | Code every service imports: errors, domain types, settings, logging, tracing, correlation ids; models, schemas and the message contract later |
| `tests/unit/<package>/` | Unit tests, no I/O |
| `tests/integration/` | Real Postgres, Redis and RabbitMQ through testcontainers (Docker needed) |
| `tests/support/` | Fakes and contract suites shared by unit and integration tests (importable as `support`) |
| `tests/architecture/` | Runs the import-linter contracts from `pyproject.toml` |
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
the images pinned in `deploy/docker-compose.yml`).
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
