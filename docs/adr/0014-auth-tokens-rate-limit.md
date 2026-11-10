# 0014 — Auth: admin-issued client credentials, short-lived access tokens, rotating refresh tokens, per-user rate limit

- Status: Proposed
- Date: 2026-11-10
- Deciders: project owner
- Brief reference: §3.3 (`POST api/login`), §3.4.3, §3.8, §3.9

## Context and problem statement

Every IDE window holds one long-lived gRPC `Session` stream (ADR-0013) and calls a few REST routes under `/api/v1`. The gateway must know who is calling, refuse unknown or disabled users, and limit each user to 200 requests per minute by default. The owner decided (Q11, 2026-11-10) that an admin creates client credentials per developer, and that SSO (Google Workspace, Entra ID) may come later. Processes stay stateless (brief §3.8, rule 03).

## Decision drivers

- Stateless gateway replicas: checking a token must not need a database round trip on every message.
- Revocation within minutes when a developer leaves or a laptop is lost.
- A stolen refresh token stops working once it has been reused.
- Several IDE windows of one developer work side by side.
- OIDC can be added without changing the stream or the clients' token handling.
- No secret is stored in clear, on the server or on the client disk. Nothing about the request body is logged (ADR-0011).

## Considered options

1. **Signed short-lived access tokens (JWT, EdDSA) plus opaque rotating refresh tokens per window, stored as HMACs.**
2. Opaque access tokens looked up in Redis on every call.
3. Long-lived API keys per developer, with no refresh.
4. OIDC from the start, with an external identity provider.

## Decision outcome

Chosen option: **1**, because it keeps the hot path local (one signature check) while revocation stays bounded by the access-token lifetime, and is immediate for refresh and streams.

### Credentials

- An admin runs a backend CLI (`create-client`, `disable-client`, `rotate-secret`). It creates a `users` row with `client_id` and a random 256-bit `client_secret`, and prints the secret once.
- The secret is stored as `HMAC-SHA256(pepper, secret)` with the pepper id (`users.client_secret_hash`). A slow password hash adds nothing for a 256-bit random secret, and would make the login endpoint a CPU target. The model comment in `tenancy.py` is updated in slice 5.
- The admin dashboard (Phase 7) calls the same service.
- The extension keeps `client_secret` and its refresh token in VS Code `SecretStorage` (the OS keychain), never in settings or files.

### Endpoints

- **`POST /api/v1/auth/token`** takes `{client_id, client_secret}` and returns `{access_token, refresh_token, expires_in}`.
  - The error is the same fixed error for an unknown id, a wrong secret and a disabled user.
  - The comparison uses `compare_digest`, with a dummy hash for unknown ids.
  - Each call starts a new **token family** (one per IDE window).
- **`POST /api/v1/auth/refresh`** revokes the presented token and issues a new pair in the same family.
  - Revocation is atomic: `UPDATE … SET revoked_at = now() WHERE token_hash = :h AND revoked_at IS NULL RETURNING …`, so two concurrent refreshes cannot both succeed.
  - Presenting an already-revoked token revokes the whole family, closes its streams and deletes its session keys (ADR-0015). The event is logged with the user id and family id only.
- **`POST /api/v1/auth/session-key`** is specified in ADR-0015.

### Access token

- A JWT signed with Ed25519, lifetime 15 minutes (`AUTH_ACCESS_TTL_S`).
- Claims: `iss`, `aud = xype-gateway`, `sub` (user id), `team`, `role`, `fam` (family id), `iat`, `exp`, `jti`.
- The header's `kid` is resolved only against the local key set (`AUTH_SIGNING_KEY_CURRENT`, `AUTH_SIGNING_KEY_PREVIOUS`). The `jku`, `x5u` and `jwk` headers are ignored.
- The algorithm is pinned to EdDSA per key, and `none` and every other algorithm are refused.
- `iss`, `aud` and `exp` are checked with 30 seconds of clock skew.
- Keys come from the environment now and from Docker secrets before hosting (plan §3a).

### Refresh token

- An opaque random 256-bit value, valid for 30 days of inactivity (`AUTH_REFRESH_TTL_S`). A family also has an absolute limit of 90 days, after which the client must log in again.
- It is stored as `HMAC-SHA256(pepper, token)` in `refresh_tokens.token_hash`. A new `family_id` column is added in slice 5.
- Revoked rows are kept until `expires_at`, so reuse can still be detected.
- Peppers (`AUTH_PEPPER_CURRENT`, `AUTH_PEPPER_PREVIOUS`) are checked current first, then previous. Retiring a pepper forces the affected clients to log in again.

### Stream

- The access token travels in the `authorization` metadata when the stream opens. The server then checks that the user is still active (ADR-0013).
- Before `exp` the server sends `TokenRefreshRequired`. The client refreshes over REST and reopens the stream, and the server closes a stream whose token has expired.
- Disabling a user, or revoking a family, publishes a revocation on the Redis channel `xype:auth:revoked` that every gateway replica subscribes to. Each replica closes the matching streams.

### OIDC-ready seam

`CredentialVerifier` (in `services/auth`) turns a credential into a `Principal` (user id, team, role). The first adapter checks client credentials. An OIDC adapter would check an identity provider's ID token instead and map it to the same `Principal`. Our own token issuer and the stream stay unchanged.

### Rate limits

- **Per user.** One Redis-backed limiter, using GCRA in a single Lua script (atomic, one round trip).
  - The default is 200 requests per minute (`RATE_LIMIT_PER_MINUTE`), and the value is shown in `HelloAck`.
  - Each REST call and each client-initiated stream message counts once.
  - A refused request gets `429`, or `StreamError.CODE_RATE_LIMITED` with `retry_after_ms`.
  - The key holds the user id only, never a token.
- **Unauthenticated.** Token, refresh and stream-open attempts are limited per source IP, plus an increasing backoff per `client_id` after failures. The backoff slows guessing without letting an attacker lock a real developer out.

### Consequences

- Good: verifying a token is a local signature check. Replicas share no state beyond Redis for limits and revocation messages.
- Good: SSO later means one new `CredentialVerifier` adapter; clients and the stream keep the same token handling.
- Good: each window has its own family, so concurrent refreshes in two windows never trip reuse detection.
- Bad: a revoked user keeps a valid access token for REST calls for up to 15 minutes. Streams close at once.
- Bad: one new dependency on the server (a JWT library; Ed25519 and HMAC come from the crypto library of ADR-0015), named and justified in slice 5.
- Bad: Redis becomes required for every request. If Redis is down the gateway fails closed with `503`, not open.

### Confirmation

Slice 5 tests:
- an expired, wrongly signed, wrong-`aud`, unknown-`kid`, `alg=none` or `jku`-carrying token is refused;
- a disabled user cannot log in, refresh or open the stream, and their open streams close;
- refresh rotates, two concurrent refreshes give one success, and reusing an old token revokes the family;
- two families of one user refresh independently;
- request 201 within one minute is refused;
- no secret or token appears in logs, traces, Redis keys or the database (privacy test).

## Pros and cons of the options

### 2. Opaque access tokens in Redis

- Good: instant revocation.
- Bad: a Redis lookup on every message, and one more store holding bearer secrets.

### 3. Long-lived API keys

- Good: simplest.
- Bad: a leaked key works until someone notices; there is no rotation and no path to OIDC.

### 4. OIDC now

- Good: no credentials of our own to manage.
- Bad: no identity provider is chosen (Q11), and it cannot be tested locally without an external account.

## More information

- ADR-0013 (stream authentication), ADR-0015 (session keys and revocation), ADR-0010 (settings), ADR-0011 (metadata-only logs).
- Schema: `users`, `refresh_tokens` in `server/common_lib/models/tenancy.py`. `refresh_tokens.family_id` arrives in slice 5.
