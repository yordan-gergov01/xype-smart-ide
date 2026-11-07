---
name: new-module
description: Scaffold a new XYPE server module (Python package xype_<name>) with Protocol interface, in-memory fake, real adapter stub, shared contract tests and README, within size limits and import boundaries. Use when adding a module or a new external dependency seam.
---

# New module

Target layout (`server/<name>/`):

```
server/<name>/
├── pyproject.toml            # package xype_<name>, workspace member
├── README.md                 # purpose, public interface, adapters, depth rationale
├── src/xype_<name>/
│   ├── __init__.py           # re-exports ONLY the public interface + domain types + errors
│   ├── interface.py          # typing.Protocol(s)
│   ├── types.py              # frozen dataclasses
│   ├── errors.py             # <Name>Error base + specific errors
│   ├── fake.py               # in-memory adapter
│   └── adapters/<tech>.py    # real adapter(s)
└── tests/
    ├── contract.py           # shared suite: def run_contract(make_impl) …
    ├── test_fake.py          # runs the contract on the fake (unit)
    └── test_<tech>.py        # runs the contract on the real adapter (@pytest.mark.integration)
```

Steps:
1. Check `docs/interfaces.md` — the interface may already be specified; reuse it exactly.
2. Justify depth: what complexity does the module hide? If none, don't create it.
3. Write the contract tests first through the Protocol, then the fake, then the adapter.
4. Add the package to the uv workspace and an import-linter contract (others import only `xype_<name>`).
5. Wire adapters only in `server/apps/*` composition roots.
6. Update `docs/glossary.md`, `docs/traceability.md`, ADR if a technology was chosen.

## Checklist
- [ ] Protocol small and documented
- [ ] Fake + real adapter pass the same contract suite
- [ ] Import boundary contract added
- [ ] Every file ≤ 500 lines, functions ≤ 50
- [ ] README + docs updated
