# 0013 — Single bidirectional gRPC `Session` stream with oneof envelopes

- Status: Proposed
- Date: 2026-11-08
- Deciders: project owner
- Brief reference: §3.3, §3.5

## Context and problem statement

The IDE and the server exchange many kinds of messages: completions on the sync path, streamed chat deltas, server requests for exact chunk content, agent tool calls the client executes, task progress and Merkle sync. Several are server-initiated. The contract must be versioned, checked for breaking changes and private by construction.

## Decision drivers

- Server-initiated messages without polling.
- One authenticated connection per IDE window, cheap to keep alive.
- Breaking changes detected in CI.
- No plaintext content in any plain field.

## Considered options

1. **One bidirectional `SessionService.Session` stream; `SessionRequest` / `SessionResponse` envelopes with a `oneof body`.**
2. One unary or server-streaming RPC per use case, plus a separate stream for server requests.
3. WebSockets with JSON messages.

## Decision outcome

Chosen option: **1**.

- Package `xype.v1` in `proto/xype/v1/`, one file per concern: `common`, `session`, `completion`, `chat`, `agent`, `index`, `feedback`.
- Every message carries an `Envelope` (message id, correlation id, W3C trace parent, send time).
- Any field that carries code, file paths, queries or answers is an `EncryptedPayload` (AES-256-GCM, client-side keys, ADR-0015); plain fields are ids, HMACs, sizes, enums and fixed server-side strings only.
- Payload rules (refined by ADR-0015): one key per direction, a 96-bit counter nonce per encryption that the receiver requires to increase within the key's lifetime, and keys rotated by the server's age and use limits. The AAD is a length-prefixed encoding that binds direction, key id, message id, request id, field number and the `ChunkRef`; the receiver rebuilds it from the plain fields. The server checks that the `key_id` belongs to the stream's authenticated principal and to this stream only, and every `repository_id` to its team.
- Selective fetch (Phase 4): the client answers a `ChunkRequest` only for refs inside the active request's scope, within a per-request count and byte cap, and refuses the rest; a test covers the refusal.
- Authentication: a bearer access token in the `authorization` metadata of the stream call, checked when the stream opens. Before expiry the server sends `TokenRefreshRequired`; the client refreshes over REST and reopens the stream, and the server closes a stream whose token expired.
- buf v2: `STANDARD` lint and `buf format` in `npm run check` and pre-commit; `FILE` breaking rules against the base branch in CI once it holds the module. Generated stubs are not committed.
- REST under `/api/v1` reuses these messages through the proto3 JSON mapping (listed in `session.proto`).

### Implementation notes (Phase 2 slice 6a)

- **Code generation.**
  - `devtools.gen_proto` (grpcio-tools) writes `server/gen/xype/v1/*_pb2.py` and `.pyi` files. They are never committed; pre-commit, CI and the image's `codegen` stage run it.
  - The runtime image has only the output and `grpcio`, never the generator.
  - No service stubs are generated. `backend.grpc` registers its one method with `grpc.method_handlers_generic_handler`, which keeps it under mypy strict. The generated `.pyi` files are exempt from strict errors only.
  - Only `backend.grpc` may import the generated modules (import contract).
- **Server.**
  - `grpc.aio` runs in the backend process, started and stopped by the app's lifespan, on `GRPC_LISTEN_ADDRESS`. In the stack that is port 50051, reached only from the edge proxy.
  - It serves TLS with the backend's certificate. It reads the certificate and key once at startup (a short blocking read in the lifespan).
  - Shutdown gives open streams `STREAM_SHUTDOWN_GRACE_S` (5 s, at most 10 s; `stop_grace_period` is 30 s).
  - Logs on a stream carry its `session_id` and a valid `x-correlation-id` from the call's metadata.
- **Layers.**
  - `backend.grpc` is transport only. `backend.services.session` holds the rules: `SessionGate` and `StreamTimers`, a pure state machine tested on a fixed clock.
  - `RepositoryAccess` (DAL `SqlRepositoryAccess`, a fake, and a shared contract) answers which repositories a team may use.
- **Opening.** A missing, malformed or invalid bearer token aborts the call with `UNAUTHENTICATED` (`invalid_token`). The rate limit gives `RESOURCE_EXHAUSTED`, and a missing store gives `UNAVAILABLE`. Opening counts as a request against the per-user limit.
- **Hello first,** within `STREAM_HELLO_TIMEOUT_S` (10 s). The gate checks the following, in order:
  - the repository ids: unique UUIDs, at most `STREAM_MAX_REPOSITORIES`;
  - `repository_roots` (new field 6): a subset of `repository_ids`, each 64 hex characters; validated now, compared with the server's roots in slice 7;
  - that every repository belongs to the caller's team;
  - that the session key exists and belongs to the caller's user **and token family**, so a key from another IDE window is refused;
  - that the key is younger than 12 h (`check_key_age`);
  - then `bind_stream`.

  Any refusal before a valid Hello ends the stream with a `StreamError`.
- **Later messages.** Each one counts against the per-user limit and checks the key's age again. The top-level repository id of `ChunkUpload` and `MerkleTreeReport` must be one authorized at Hello. Nested `ChunkRef`s and the other messages that name repositories are checked by the slice that handles them; until then they are refused as unsupported.
  - Messages whose handling has not landed yet are refused (`message not supported yet`), and the stream stays open.
  - A key past its age ends the stream.
- **Timers.**
  - The client sends `Heartbeat` every `heartbeat_interval_s` (20 s, announced in `HelloAck`) and the server echoes it. After 60 s of silence the server closes with `CODE_TIMEOUT`. Settings refuse an idle timeout below twice the heartbeat interval.
  - Only a successful Hello stops the 10 s Hello deadline.
  - `TokenRefreshRequired` goes out 60 s before the token expires, and the stream closes with `CODE_UNAUTHENTICATED` at expiry. A stream therefore lives at most 15 minutes, and a reconnect mints a new session key.
  - Revocation closes streams at once (slice 6b, below).
- **Limits per stream.**
  - Messages are capped at 1 MiB, and at most 2 are read ahead, so one stream holds about 3 MiB at most. HTTP/2 flow control holds back a client that sends faster.
  - At most `STREAM_MAX_CONCURRENT` (32) streams per backend process; beyond that, new calls get `RESOURCE_EXHAUSTED`.
  - Five rate-limited messages in a row close the stream (`rate_limited`).
  - At most 16 streams per HTTP/2 connection, and pings no more often than every 10 s.
  - nginx allows 64 open streams per client IP.
- **Errors.**
  - `StreamError.message` is a fixed string per refusal.
  - An unexpected error ends the stream with `CODE_INTERNAL` (`internal error`) and logs the exception type only.
  - The reader task's end is always collected, and so is the pending read when a stream is cancelled. A failed read logs its type only and closes the stream as `read_failed`.
  - Responses carry an `Envelope` with a UUIDv7 message id and the request's message id as `correlation_id`.
  - Logs and spans carry ids and outcomes only.
- **Metrics:** `xype_grpc_stream_opens_total{outcome}`, `xype_grpc_streams_active`, `xype_grpc_stream_messages_total{kind,outcome}`, `xype_grpc_streams_closed_total{reason}` and `xype_grpc_stream_duration_seconds`.
- **Edge proxy.**
  - `grpc_pass grpcs://` to the backend, with `grpc_ssl_verify` against the dev CA and the name `backend`.
  - Client trace headers are dropped.
  - Stream opens are limited to 20 a minute per IP, burst 10. Past that, a trailers-only `RESOURCE_EXHAUSTED` answer.
  - No request body limit for the stream (`client_max_body_size 0`); the backend caps each message.
  - Timeouts are 120 s, above the backend's idle timeout. A stream with heartbeats stayed open past 60 s through the proxy in the live check.

### Implementation notes (Phase 2 slice 6b)

- **Stream registry.** Each process keeps its open streams by user and by token family (`StreamRegistry`), from the moment a stream opens, so a stream is revocable even before Hello. A stream leaves the registry when it closes.
- **Revocation channel.**
  - Every backend process subscribes to `xype:auth:revoked` (`RedisRevocationSubscriber`, the backend's own ACL user: `+subscribe +unsubscribe` on that channel only; `PSUBSCRIBE` and other channels are refused).
  - A revocation ends the matching streams with `CODE_UNAUTHENTICATED` (`access revoked`).
  - The subscriber reconnects with a capped backoff (0.5 s to 10 s) and skips anything on the channel that is not a revocation, logging the type only.
- **Key check on every message (the backstop).**
  - Pub/sub delivers at most once: a message sent while a process reconnects is lost.
  - Revocation deletes the session keys before it publishes (ADR-0015), and every stream message checks that its key still exists and is still bound to this stream (`bind_stream`, idempotent).
  - So a revoked stream ends at its next message (heartbeats every 20 s) even without the message.
  - Side effects:
    - a key store outage ends every stream at its next message (`CODE_UNAVAILABLE`, fail closed);
    - a user's ninth window evicts the oldest key (ADR-0015), and that window's stream now ends at its next message. The extension must not reconnect in a loop on `session key unknown, expired or not yours`, and should tell the user there are too many windows (slice 11).
- **Drain on shutdown.**
  - The lifespan tells every stream `CODE_UNAVAILABLE` (`server restarting`) first, and any stream opened from then on at once.
  - Then the gRPC server gives them the grace period, the listener stops, and the stores close.
  - Clients reconnect, through the edge, to another replica or the restarted one.
- **New code** `StreamError.CODE_UNAVAILABLE = 8` ("reconnect with backoff"), also used when a store is down (it was `CODE_INTERNAL`).

### Consequences

- Good: server requests, tool calls and progress share one connection with one auth and one rate limit; the contract is checked mechanically.
- Bad: a long-lived stream needs heartbeats, reconnects and drain on deploy; load balancers must support HTTP/2 streams (the edge proxy will use `grpc_pass`).
- Bad: one envelope grows with every message kind; each new kind is a new `oneof` field.

### Confirmation

`npm run proto` (buf lint + format) in pre-commit and CI; `buf breaking` on pull requests against the base branch.
