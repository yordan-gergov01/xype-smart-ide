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

### Consequences

- Good: server requests, tool calls and progress share one connection with one auth and one rate limit; the contract is checked mechanically.
- Bad: a long-lived stream needs heartbeats, reconnects and drain on deploy; load balancers must support HTTP/2 streams (the edge proxy will use `grpc_pass`).
- Bad: one envelope grows with every message kind; each new kind is a new `oneof` field.

### Confirmation

`npm run proto` (buf lint + format) in pre-commit and CI; `buf breaking` on pull requests against the base branch.
