# 0012 — Paid-API guard: network guard in tests, hooks and CI checks

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner (pending), Claude Code
- Brief reference: §0 Cost rule, §1, §3.7, §5

## Context and problem statement

Paid models (OpenRouter, DeepSeek) stay off until the owner approves Phase 10 (ADR-0005), and the repository is public. One accidental call during development, tests or CI costs money and sends code to a third party (risk R10 in `docs/plan.md`). No single check covers every path: code can name an endpoint, configuration can enable a provider, a test can open a socket, and the agent can run a shell command. This decision sets the guards in place before the first adapter for a remote service exists. It also adds the dependency scan and SBOM that rule 07 asks for.

## Decision drivers

- Fail before anything leaves the machine, not after a bill or a log entry.
- One list of paid endpoints (`tools/paid_api_hosts.txt`) shared by every guard.
- Works the same on the Windows dev machine and on Linux CI.
- Read-only CI permissions; no extra secrets.

## Considered options

1. Layered guards: shell hook, a source check, a configuration check and a network guard inside the test process.
2. A third-party pytest plugin that blocks sockets (pytest-socket and similar).
3. Network isolation of the CI runner only (firewall or no-network containers).

## Decision outcome

Chosen option: **1, layered guards**, because each layer covers a path the others miss and all of them use the same host list.

| Layer | Where | What it refuses |
|---|---|---|
| Shell hook | `.claude/hooks/pre-bash-guard.mjs`, `.claude/settings.json` | Agent shell commands and WebFetch to a paid host |
| Source check | `tools/check_paid_api.py` (pre-commit, CI) | A paid endpoint named outside docs and the guard tooling |
| Configuration check | `tools/check_paid_config.py` (pre-commit, CI) | Tracked env templates, compose files or workflows that enable paid models: `PAID_MODELS_ENABLED` not explicitly false, a paid provider key, or `LLM_PRIMARY`/`LLM_FALLBACK` naming a paid provider, also through a compose default (`${VAR:-true}`) or a CI secret (`${{ secrets.X }}`). It reads only git-tracked files and never follows a symlink, so it never opens a real `.env` |
| Network guard | `server/tests/support/network_guard.py`, installed by `tests/conftest.py` for the whole session | Any connection from the test process to a host that is not this machine |

Network guard details:
- It patches the forward lookups (`getaddrinfo`, `gethostbyname`, `gethostbyname_ex`), `socket.connect`, `connect_ex`, `sendto` and `sendmsg`, and, on Windows, asyncio's IOCP proactor `connect` and `sendto` (the default Windows event loop does not go through the `socket` methods). A paid host is refused before it is even resolved. A name given at connect time is resolved, and every address must be local.
- It allows loopback, `localhost`, `*.localhost`, Unix sockets (the Docker API) and the Docker host that testcontainers uses (`tcp://` `DOCKER_HOST`, `TC_HOST`, `TESTCONTAINERS_HOST_OVERRIDE`), resolved to its addresses once at install. Proxy variables are removed for the session, so nothing relays through a local proxy.
- The error is a `RuntimeError` subclass, not an `OSError`, so HTTP clients do not turn it into a retryable connect error. Every refusal is recorded. A test that swallows the error still fails; a refusal in a wider-scoped fixture fails the next test, and one outside any test fails the session.
- The guard's own tests use addresses reserved for documentation (`192.0.2.1`) and `.invalid` names, and read the paid hosts from the shared list. A broken guard therefore still reaches nobody.

Supply chain (rule 07):
- **Dependency scan:** OSV-Scanner on `server/requirements/*.txt` and `package-lock.json`. An accepted vulnerability goes into `osv-scanner.toml` with a reason and an expiry date; when the date passes, CI fails again.
- **SBOM:** CycloneDX from Syft (`anchore/sbom-action`), kept as a workflow artifact. Dependency snapshots and release uploads are off, so the workflow stays `contents: read`.
- Both actions are pinned by commit SHA. The OSV-Scanner action runs a container image pinned by tag inside that commit, and the SBOM action downloads Syft at run time; both run with a read-only token that checkouts do not keep (`persist-credentials: false`).

CI also runs the testcontainers integration tests (Postgres + pgvector, probes). The embedder test needs the model volume that compose downloads, so it carries a `models` marker and runs on developer machines only.

### Consequences

- Good: an accidental paid call fails at the earliest point on every path: shell, source, configuration and test process.
- Good: one host list. The hooks test keeps the list and the settings denies in sync.
- Bad: the network guard covers the Python test process only:
  - A container a test starts has its own network; the Ollama test container sits on Docker's default bridge, with cloud models off.
  - A model server already running on this machine is reachable on loopback.
  - Native code that opens sockets without the `socket` module (grpcio, when gRPC tests arrive: then add a no-egress network for the CI test job), direct `_socket` use and subprocesses are not covered.
  - Reverse lookups are not guarded, and a `*.localhost` lookup may reach the system's DNS resolver (the name only).
  - The configuration check reads one assignment per line; a value set inside a command line, a YAML flow collection or a block scalar is not parsed.
- Bad: the embedder integration test is not run in CI.
- Bad: accepted vulnerabilities need a manual review when they expire.

### Confirmation

- `server/tests/architecture/test_network_guard.py`:
  - every paid host is refused;
  - sync sockets, `connect_ex`, addressed UDP sends, legacy lookups, async httpx by address and by name, and asyncio's own connect are refused (run locally on Windows; Linux in the CI `python` job);
  - the Docker host is allowed by name and by address only when the environment names it;
  - loopback still works.
- `tools/test_repo_checks.py` covers the configuration check.
- The integration suite passes with the guard on (testcontainers and Ryuk included).
- The CI jobs `repo-checks`, `python`, `integration`, `dependency-scan` and `sbom`.

## Pros and cons of the options

### Layered guards

- Good: covers the agent, the code, the configuration and the tests; no new runtime dependency.
- Bad: more small tools to keep correct, each with its own tests.

### A socket-blocking pytest plugin

- Good: maintained by others.
- Bad: one more dependency. It also needs a check that it handles Windows' proactor loop. It covers the test process only, so the other layers are still needed.

### Isolating the CI runner

- Good: covers containers too.
- Bad: CI only. A developer's machine, where most tests run, stays open. Testcontainers needs to pull images, so the runner cannot simply be offline.

## More information

- ADR-0005 (paid access and its enablement guard), ADR-0034 (compose networks; the backend has no egress).
- `tools/paid_api_hosts.txt`, `.claude/rules/06-testing.md`, `.claude/rules/07-security-privacy.md`.
