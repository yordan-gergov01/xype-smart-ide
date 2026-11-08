# 0009 — No DI framework: constructor injection in composition roots

- Status: Proposed
- Date: 2026-11-08
- Deciders: project owner
- Brief reference: §2

## Context and problem statement

Every external dependency sits behind a Protocol with a real adapter and a fake (brief §2). Something has to choose the adapter for each run: real services in containers, fakes in unit tests.

## Decision drivers

- Wiring readable in one place per process.
- Tests swap adapters without patching.
- No framework magic for a small number of services.

## Considered options

1. **Constructor injection, wired by hand in each composition root.**
2. A DI container (`dependency-injector`, `punq`).
3. FastAPI `Depends` all the way down.

## Decision outcome

Chosen option: **1**.

- Services take their collaborators as constructor arguments typed by Protocols.
- Composition roots: `backend/main.py` (`create_app`), later `llm/main.py` and `indexer/main.py`. They build every adapter and service once at startup.
- Routes get services through small functions in `backend/api/dependencies/services.py` that read them from the app state. Tests pass fakes to `create_app` or override those functions.
- `create_app` accepts optional fakes (today: readiness probes); everything else comes from settings.

### Consequences

- Good: plain Python; one file shows what a process is made of; no extra dependency.
- Bad: wiring code grows with the number of services and is written by hand.

### Confirmation

import-linter keeps services free of adapters (`backend.services` may not import `backend.core`, `backend.dal`, `backend.messaging`, stores or transports); unit tests build the app with fakes only.
