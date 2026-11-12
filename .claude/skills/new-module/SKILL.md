---
name: new-module
description: Scaffold a new server feature (a brief module such as retrieval, cache or router) in the template_core layout - service with Protocols, repository, transport, fakes, contract tests and import-linter contract - within size limits. Use when adding a module or a new external dependency seam.
---

# New module (server feature)

Layout follows template_core (ADR-0006). A brief module is a **feature** spread over layers:

```
server/
├── common_lib/schemas/<feature>.py          # request/response models (Pydantic, extra="forbid")
├── backend/
│   ├── api/v1/<feature>/router.py            # REST transport only (if the feature has endpoints)
│   ├── grpc/<feature>.py                     # gRPC handlers only (if on the Session stream)
│   ├── services/<feature>/
│   │   ├── __init__.py                       # re-exports the public interface, types, errors
│   │   ├── interface.py                      # typing.Protocol(s) it needs and offers
│   │   ├── errors.py                         # <Feature>Error(XypeError) + specific errors
│   │   └── service.py                        # business rules + build_<feature>_service()
│   └── dal/<feature>/repository.py           # Postgres/Redis access; returns plain records
└── tests/
    ├── support/<feature>.py                  # in-memory fakes (importable as `support`)
    ├── support/contracts/<feature>.py        # shared contract suite: assert_* functions
    ├── unit/backend/<feature>/test_*.py      # service rules with fakes; contract on the fake
    └── integration/test_<feature>_*.py       # contract on the real adapter (@pytest.mark.integration)
```

Workers (`llm/`, `indexer/`) follow the same idea: `providers/` or adapters behind a Protocol, `main.py` as composition root, no database access. A production stand-in that is configured, not just used in tests (`MockLLMProvider`), lives with the adapters; test-only fakes live in `tests/support`.

Steps:
1. Check `docs/interfaces.md` — the interface and its location may already be specified; reuse them exactly.
2. Justify depth: what complexity does the feature hide? If none, don't create it.
3. Write the contract tests first through the Protocol, then the fake, then the adapter.
4. Add an import-linter contract in `server/pyproject.toml` (other features import only `backend.services.<feature>`; transports never import `dal`).
5. Wire adapters only in `backend/main.py` / `backend/api/dependencies/services.py` (or the worker's `main.py`).
6. Add a direct dependency only to the right `server/requirements/<service>.in` and recompile (ADR-0007).
7. Update `docs/glossary.md`, `docs/traceability.md`, ADR if a technology was chosen.

## Checklist
- [ ] Protocol small and documented
- [ ] Fake + real adapter pass the same contract suite
- [ ] Import-linter contract added; `tests/architecture` green
- [ ] Every file ≤ 500 lines, functions ≤ 50
- [ ] Docs updated
