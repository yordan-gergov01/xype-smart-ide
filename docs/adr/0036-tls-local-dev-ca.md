# 0036 — TLS in transit with a local dev CA

- Status: Proposed
- Date: 2026-11-11
- Deciders: project owner
- Brief reference: §3.7 (encryption in transit, including locally via a dev CA)

## Context and problem statement

From Phase 2 the stack carries payloads: encrypted code on the gRPC stream, tokens, and
later queries and answers to and from the backend. Until now every hop in the local stack
was plaintext, protected only by internal Docker networks. Brief §3.7 asks for TLS
everywhere, locally too, with a dev CA. The setup has to work on Windows (Docker Desktop)
and Linux (CI), must not commit any key, and must not depend on a tool that is not
already installed.

## Decision drivers

- No plaintext path for payloads, enforced by the servers, not only by client settings.
- Clients really verify: the chain and the host name, not just "encrypted".
- One CA for the whole stack, created with one command, and nothing in Git.
- A leaked dev CA key must not let anyone impersonate hosts outside the stack and
  loopback on a developer machine that trusts it.

## Considered options

1. **A Python generator (`server/devtools/dev_ca.py`, `cryptography`) writing one CA and
   one leaf per service into the ignored `deploy/certs/`; TLS-only listeners.**
2. `mkcert`: a further tool to install on every machine, and its CA has no name
   constraints.
3. `openssl` shell scripts: Git Bash rewrites `-subj "/CN=…"` into a Windows path, and
   the options differ between platforms.
4. A one-shot compose service that issues certificates into a volume: the IDE on the host
   still needs the CA certificate copied out, and the CA key would live in a volume.

## Decision outcome

Chosen option: **1**.

- **CA**: ECDSA P-256, valid 2 years, `pathlen:0`, and critical **name constraints**: it
  may issue only for the stack's service names and `localhost` (and `*.localhost`).
  IP names are not constrained (Phase 2 slice 6a, owner's decision 2026-11-12):
  - grpcio's TLS stack (BoringSSL) refuses any chain whose CA has an IP name constraint;
  - leaves keep their `127.0.0.1` name, so a leaked key could sign for any IP address;
  - the generator refuses an older CA that still has the constraint and asks for
    `--rotate-ca`.

  A leaked key can therefore impersonate a stack service to a client that trusts this
  CA, which is why the CA is never imported into an OS trust store
  (`docs/runbooks/local-dev.md`). The CA key stays in `deploy/certs/ca.key` and is
  never mounted into a container. File modes protect the keys on Linux and macOS (folder
  0700, CA key 0600). On Windows, `chmod` only sets the read-only flag: the keys are as
  readable as the checkout's folder, so keep the repository under the user profile, or
  restrict `deploy\certs` with `icacls`.
- **Leaves**: one per TLS server (`edge`, `backend`, `postgres`, `redis`, `rabbitmq`),
  ECDSA P-256, valid 90 days, `serverAuth`, with SANs for the service name, `localhost`
  and `127.0.0.1` (healthchecks and tests connect over loopback). Each service mounts only
  its own directory (`tls.crt`, `tls.key`, `ca.crt`) read-only at `/certs`; the migration
  job gets `ca.crt` alone. Re-running the generator renews leaves with fewer than 30 days
  left; `--rotate-ca` replaces everything (`docs/runbooks/local-dev.md`).
- **Servers have no plaintext listener**:

  | Server | Configuration |
  |---|---|
  | Edge proxy | `listen 8443 ssl`, `http2 on` (for gRPC in slice 6), TLS 1.2–1.3. It verifies the backend: `proxy_ssl_verify on` against the dev CA, name `backend`. |
  | Backend | uvicorn `--ssl-certfile/--ssl-keyfile` in the image's command, so it does not start without a certificate. |
  | Postgres | `ssl=on`, TLS ≥ 1.2. `pg_hba.conf` (outside the data volume) has only `hostssl … scram-sha-256` and `hostnossl … reject`. An entrypoint copies the key into the tmpfs with mode 0600, because Postgres refuses a world-readable key and bind mounts on Docker Desktop look world-readable. |
  | Redis | `port 0`, `tls-port 6379`, `tls-auth-clients no` (ACL passwords, no client certificates). |
  | RabbitMQ | `listeners.tcp = none`, AMQPS on 5671, `verify_none` for clients (passwords). The management API listens on the container's loopback only, because it can read messages. |

- **Clients**: `common_lib.tls.client_context` holds the client policy: the dev CA
  only, host-name check, TLS ≥ 1.2. asyncpg (backend engines and migrations) and aio-pika
  take it. redis-py accepts no `ssl.SSLContext`, so it is given the same settings (CA
  file, `ssl_cert_reqs="required"`, `ssl_check_hostname=True`, `MINIMUM_TLS`). Prometheus
  scrapes the backend's `/metrics` over HTTPS with the CA certificate alone. The gRPC
  client in the extension follows the same policy. `TLS_CA_FILE` is required: there is no plaintext
  fallback, so a service without a CA does not start.
- **Tests**: the integration tests run the stores with the compose file's images, users,
  entrypoints and configuration, and certificates from a throwaway CA. Each store must
  refuse plaintext and a server certificate from another CA. Unit tests cover the
  host-name check and the name constraints on a loopback TLS server.
- **Not covered**:
  - Ollama stays on HTTP on the internal `models` network (ADR-0002).
  - The OTel collector's metrics and the Prometheus and Grafana UIs stay HTTP; they carry
    no payloads. The backend's `/metrics` is scraped over verified HTTPS.
  - RabbitMQ's Erlang distribution (25672) and epmd (4369) stay plaintext, protected by
    the Erlang cookie. They are reachable on the internal `messaging` network, whose only
    other member is the backend. They carry management commands, not messages.
  - The `keystore` Redis (slice 8) gets its own leaf from the same generator.
  - Hosted certificates come from the hosting environment's CA.

### Consequences

- Good: no payload crosses any hop in plaintext, and the servers enforce it.
- Good: the tests exercise the shipped configuration and prove each store refuses
  plaintext.
- Good: the name constraints limit the damage if a developer's CA key leaks.
- Bad: one more setup step, and leaves expire every 90 days. A missing directory fails
  the start: compose cannot create the bind source on Linux, and on Docker Desktop the
  entrypoints and servers stop on missing files.
- Bad: every TLS hop costs a handshake. The pools keep connections open, so the cost is
  one handshake per connection.
- Neutral: clients outside the stack (curl, the IDE) must trust `deploy/certs/ca.crt`.
  curl on Windows also needs `--ssl-no-revoke`, because a dev CA has no revocation list.
