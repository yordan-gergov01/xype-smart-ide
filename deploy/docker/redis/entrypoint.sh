#!/bin/sh
# Renders the Redis config into the tmpfs at /data and starts the server with it,
# so no password ever appears on a command line. Cache only: no persistence,
# bounded memory, least recently used keys evicted. Compose invokes this script
# through `sh`.
#
# Users: `default` (password REDIS_PASSWORD) is for the container healthcheck and
# operators; each service gets its own ACL user: read, write and connection
# commands only (no dangerous, admin or scripting ones), keys under `xype:` only,
# no pub/sub channels.
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

for name in REDIS_PASSWORD REDIS_BACKEND_USER REDIS_BACKEND_PASSWORD; do
    require "$name"
done
case "$REDIS_BACKEND_USER" in
    default | [~\&+\-]*)
        echo "error: REDIS_BACKEND_USER must not be 'default' or start with ~ & + -" >&2
        exit 1
        ;;
esac

umask 077
cat > /data/redis.conf <<EOF
requirepass $REDIS_PASSWORD
user $REDIS_BACKEND_USER on >$REDIS_BACKEND_PASSWORD resetchannels ~xype:* +@read +@write +@fast +@connection -@dangerous -@admin -@scripting
save ""
appendonly no
maxmemory 64mb
maxmemory-policy allkeys-lru
EOF

exec redis-server /data/redis.conf
