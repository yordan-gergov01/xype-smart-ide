# 03 — Architecture

- Do give every module a small public interface: a `typing.Protocol` (Python) or `interface` (TS) exported from the package root. Reason: callers depend on the seam, not the internals.
- Never import another module's internals; import only its public package. Enforced by import-linter (Python) and ESLint boundaries (TS).
- Never let core code import a concrete provider, database, queue or cache. Adapters are wired only in the composition roots `server/apps/{api,grpc,worker,scheduler}` by constructor injection. No DI framework.
- Do give every external dependency three things: a real adapter, an in-memory fake, and a shared contract test both must pass.
- Do prefer **deep modules**: a simple interface hiding real complexity. If deleting a module would only move its complexity to callers, it is shallow — merge it.
- Vocabulary (use consistently): **module**, **interface**, **seam**, **adapter**, **fake**, **composition root**, **depth**.
- Keep the IDE fork (`fork/`) limited to branding, UX simplification and bundling. All AI logic lives in `clients/xype-ai`.
- Keep processes stateless (API, gRPC, workers); state lives in Postgres, Redis, RabbitMQ.
- Do keep everything environment-driven (12-factor). Never hard-code `localhost`, ports or hostnames in code.
- Retrieval and agents work only on registered XYPE repos and templates. No general web search in v1.
