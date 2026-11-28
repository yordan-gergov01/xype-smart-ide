# End-to-end run on the seeded sample repository

The Phase 2 exit check (`server/tests/e2e/test_sample_repo.py`):
1. The extension's sync worker indexes `server/tests/fixtures/sample-repo/`.
2. The path is real: the edge proxy, the gRPC `Session` stream, RabbitMQ and the indexer, into pgvector.
3. Editing one function re-embeds exactly one chunk.
4. A scan finds none of the repository's canaries on the server.

The run uses a throwaway stack: a separate compose project (`xype-e2e`) with its own volumes, port and freshly generated passwords and keys. Your own stack and its env file are not touched. Only the downloaded models are shared (volume `xype_ollama_data`).

## Run

Needs Docker, Node 24 and the server venv. `docker compose` and `docker exec` run in the Git Bash or WSL shell. `$SCRATCH` must be a private directory outside the repository (the generator refuses one inside it): the file is created owner-only on Linux and macOS, while on Windows it inherits the directory's ACL, so use a directory under your own user profile. Never run the test with `--showlocals` (`-l`): the client secret is a local variable while it registers.

```bash
# 1. The dev CA (once) and the built extension
python tools/server_tool.py python -m devtools.dev_ca
npm run build -w clients/xype-ai

# 2. Fresh configuration for the throwaway stack (new values on every run)
python tools/server_tool.py python -m devtools.e2e_stack --out "$SCRATCH"

# 3. Start it; the collector prints every span, so the scan reads the traces
docker compose -f deploy/docker-compose.yml -f deploy/e2e/compose.e2e.yml \
  --env-file "$SCRATCH/stack.vars" up -d --build --wait backend indexer edge otel-collector

# 4. The test (about a minute)
cd server && XYPE_E2E_PROJECT=xype-e2e .venv/Scripts/python -m pytest -m e2e tests/e2e

# 5. Remove the stack, its volumes and the configuration
docker compose -f deploy/docker-compose.yml -f deploy/e2e/compose.e2e.yml \
  --env-file "$SCRATCH/stack.vars" down -v
rm -r "$SCRATCH"
```

`down -v` keeps the shared model volume, because the override names it, and compose removes only the volumes it created for the project. Each test run registers a new team, client and repository, so the run can be repeated on the same stack.

## What the scan covers

| Place | How |
|---|---|
| Postgres | `pg_dump --data-only` of every table, and every file of the data directory, the write-ahead log included |
| Redis and the key store | every key name and value in every database, read by type, and the files under `/data` (append-only file, snapshots) |
| RabbitMQ | every file of the data directory (queue segments keep consumed messages), and no queue (dead-letter queues included) may still hold a message |
| Backend and indexer | every file under `/tmp` (their only writable paths) |
| Logs | `docker logs` of every service, at `LOG_LEVEL=DEBUG` |
| Traces | the collector's log, with the debug exporter at `detailed` verbosity (`deploy/e2e/collector.yaml`) |

Canaries:
- every `zqv` word of seven characters or more in the sample repository's file contents and paths, the git-ignored file included, searched as is, lowercased, in hex and in base64;
- a GitHub-style token the test plants at run time, so none is committed;
- the text the edit introduces;
- the unkeyed SHA-256 of every path, every file and every run of whole lines (any chunk the client could cut), matched against every 64-digit hex token in the data.

A positive control proves the scan reads each kind of place: known table names, an indexer event, a span, and a key it plants in both Redis stores.

A finding names the place and the canary, never the surrounding bytes.

## Troubleshooting

- **`no indexed within 120 s`**: the client could not sign in or reach the stream. Check `docker logs xype-e2e-backend-1` for `auth.*` or `grpc.*` events.
- **`the index did not settle`**: the indexer is still embedding, or it is waiting for Ollama. Look for `indexer.chunk.done` in `docker logs xype-e2e-indexer-1`.
- **Port 18443 in use**: change `BACKEND_PORT` in the generated file and pass `XYPE_E2E_PORT`.
