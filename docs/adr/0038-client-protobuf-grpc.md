# 0038 — Client protobuf and gRPC: protobuf-es and Connect-ES

- Status: Proposed
- Date: 2026-11-26
- Deciders: project owner
- Brief reference: §3.3, §3.5

## Context and problem statement

The `xype-ai` extension talks to the server over the `SessionService.Session` bidirectional gRPC stream (ADR-0013). It needs TypeScript types for `xype.v1` and a gRPC client that runs in the extension host (Node), with TLS against the dev CA (ADR-0036). Generated code must not be committed, as on the server (rule 01).

## Decision drivers

- Typed messages that pass `strict` TypeScript with `erasableSyntaxOnly` and ESLint `strictTypeChecked`.
- Bidirectional streaming over HTTP/2 with metadata (the bearer token, ADR-0013), on Node.
- An in-memory server for tests, so the session logic needs no sockets.
- Few, well-maintained dependencies; generation from npm-installed tools only, with no remote plugins.

## Considered options

1. **protobuf-es v2 with Connect-ES v2** (`@bufbuild/protobuf`, `@connectrpc/connect`, `@connectrpc/connect-node`; `@bufbuild/protoc-gen-es` to generate).
2. `@grpc/grpc-js` with `@grpc/proto-loader`: messages loaded at run time, untyped.
3. `@grpc/grpc-js` with `ts-proto` generated types.

## Decision outcome

Chosen option: **1**.

- **Generation.** `clients/xype-ai/buf.gen.yaml` runs the npm `protoc-gen-es` through `node`, so it works the same on Windows and Linux. The output goes to `clients/xype-ai/src/gen/` as `js+dts`: generated enums are not erasable syntax, and declarations are. git, ESLint, Prettier, jscpd and the size check ignore that folder.
- **Running it.** `npm run gen` runs first in `npm run check` (and therefore in CI's node job), and the pre-commit hook `gen-ts` runs it before ESLint and tsc on every commit (it takes about a second).
- **Transport.** gRPC over HTTP/2 (`createGrpcTransport`) with the server's CA in `nodeOptions.ca`; verification is never turned off. HTTP/2 pings stay off, which is connect-node's default: the server answers frequent pings with GOAWAY, and the session's 20 s heartbeat keeps the stream alive.
- **Seams.** `SessionTransport` (open a stream with a bearer token) over any Connect `Transport`. Tests use Connect's in-memory router transport, and one test runs the real transport over HTTP/2 on loopback.
- **Mapping.** Only `src/session/wire.ts` maps between the sync's plain messages (`src/sync/messages.ts`) and the generated types.
- **REST.** The four auth calls use `node:https` with the same CA, so no HTTP client library is added.

### Consequences

- Good: typed messages and a typed client from one generator, the same `buf` CLI as `npm run proto`.
- Good: the session logic is tested without sockets, and the transport over real HTTP/2.
- Bad: three runtime dependencies, which Phase 8 bundling must include with their notices.
- Bad: generated code is missing until `npm run gen` runs. A fresh clone must run it (`npm run check` does) before ESLint or tsc can resolve `@/gen/…`.

### Confirmation

- `clients/xype-ai/src/session/transport.test.ts` runs a bidirectional call over HTTP/2 and checks the bearer metadata.
- `clients/xype-ai/src/session/session.test.ts` runs the session against the in-memory router.
- CI's node job generates the code before lint, types and tests.
