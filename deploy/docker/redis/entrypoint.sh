#!/bin/sh
# Renders the Redis config into the tmpfs at /data and starts the server with it,
# so no password ever appears on a command line. Compose invokes this script through
# `sh`. No persistence in either mode. REDIS_MODE picks the instance:
# - `cache` (the `redis` service): 64 MB, least recently used keys evicted.
# - `keystore` (the `keystore` service, ADR-0015): session keys wrapped under the KEK;
#   16 MB and never evicts (a write fails instead), since an evicted key breaks a stream.
#
# Users: `default` (password REDIS_PASSWORD) is for the container healthcheck and
# operators; each service gets its own ACL user: read, write and connection
# commands only (no dangerous or admin ones). Of scripting, only EVALSHA and SCRIPT
# LOAD (the rate limiter's and the key store's scripts). In the cache: keys under
# `xype:`, and of pub/sub only PUBLISH and SUBSCRIBE on the one revocation channel
# (ADR-0014; every gateway replica listens). In the key store: keys under `xype:sk`
# only, and no pub/sub.
set -eu

require() {
    eval "value=\${$1:-}"
    if [ -z "$value" ]; then
        echo "error: environment variable $1 must be set" >&2
        exit 1
    fi
    case "$value" in
        *[[:space:]]* | *'"'* | *'\'* | *'>'* | *'<'*)
            echo "error: $1 must not contain spaces, quotes, backslashes, < or >" >&2
            exit 1
            ;;
    esac
}

for name in REDIS_MODE REDIS_PASSWORD REDIS_BACKEND_USER REDIS_BACKEND_PASSWORD; do
    require "$name"
done
case "$REDIS_BACKEND_USER" in
    default | [~\&+\-]*)
        echo "error: REDIS_BACKEND_USER must not be 'default' or start with ~ & + -" >&2
        exit 1
        ;;
esac

# Each mode: the backend user's keys and channels, the commands it gets on top of the
# common set (added last, so the category removals cannot take them back), and memory.
case "$REDIS_MODE" in
    cache)
        scope="resetchannels &xype:auth:revoked ~xype:*"
        extra="+publish +subscribe +unsubscribe"
        memory="maxmemory 64mb
maxmemory-policy allkeys-lru"
        ;;
    keystore)
        scope="resetchannels ~xype:sk*"
        extra=""
        memory="maxmemory 16mb
maxmemory-policy noeviction"
        ;;
    *)
        echo "error: REDIS_MODE must be cache or keystore" >&2
        exit 1
        ;;
esac

for file in tls.crt tls.key ca.crt; do
    if [ ! -r "/certs/$file" ]; then
        echo "error: /certs/$file is missing; create the dev CA (docs/runbooks/local-dev.md)" >&2
        exit 1
    fi
done

# TLS only (ADR-0036): no plaintext port. Clients authenticate with ACL passwords, not
# certificates.
umask 077
cat > /data/redis.conf <<EOF
port 0
tls-port 6379
tls-cert-file /certs/tls.crt
tls-key-file /certs/tls.key
tls-ca-cert-file /certs/ca.crt
tls-auth-clients no
tls-protocols "TLSv1.2 TLSv1.3"
requirepass $REDIS_PASSWORD
user $REDIS_BACKEND_USER on >$REDIS_BACKEND_PASSWORD $scope +@read +@write +@fast +@connection -@dangerous -@admin -@scripting -@pubsub +evalsha +script|load $extra
save ""
appendonly no
$memory
EOF

exec redis-server /data/redis.conf
