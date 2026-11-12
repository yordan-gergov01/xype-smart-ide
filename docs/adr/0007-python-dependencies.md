# 0007 — Python dependencies: pip with compiled, hashed requirements files

- Status: Accepted (chosen by the project owner on 2026-11-07)
- Date: 2026-11-07
- Deciders: project owner
- Brief reference: §1, §8

## Context and problem statement

The server has several services (backend, LLM worker, later the indexer) that share `common_lib` but need different dependencies. Images must be reproducible, development happens on Windows while containers run Linux, and the template_core template already solves this.

## Decision drivers

- Same install on every platform and in every image.
- Each image contains only its own service's dependencies.
- Same workflow as template_core.

## Considered options

1. **pip + `requirements/<service>.in` compiled by `uv pip compile --universal --generate-hashes`.**
2. uv workspace with one `uv.lock`.
3. pip-tools (`pip-compile`).

## Decision outcome

Chosen option: **1**.

- `server/pyproject.toml` is one project (setuptools) holding tool configuration; it declares no dependencies.
- Direct dependencies per service live in `server/requirements/<service>.in`; `dev.in` adds the tooling and includes the service files (`-r`) under their constraints (`-c`).
- Compile with `uv pip compile --universal --generate-hashes --python-version 3.12 requirements/<group>.in -o requirements/<group>.txt`; service files first, then `dev`.
- Install with `pip install --require-hashes -r requirements/<group>.txt` and `pip install -e . --no-deps` locally; each image installs only its own file.
- Python 3.12 (`>=3.12,<3.13`).

### Consequences

- Good: pinned and hash-checked installs, valid on Windows, macOS and Linux from one file.
- Good: pip is the only installer in images; uv is a development tool used for one command (it is pinned in `dev.txt`).
- Bad: adding a dependency is two steps (edit `.in`, recompile); CI fails when `.txt` is stale.

### Confirmation

CI installs with `--require-hashes`, then recompiles with the pinned uv and fails on any diff.

## Pros and cons of the options

### uv workspace

- Good: one lock, fast `uv sync`.
- Bad: uv becomes the installer everywhere, including images; differs from template_core.

### pip-tools

- Good: established.
- Bad: resolves for the host platform only, so a file compiled on Windows breaks Linux images.

## More information

- `server/README.md`, "Adding or upgrading a dependency".
