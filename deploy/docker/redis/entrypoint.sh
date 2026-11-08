#!/bin/sh
# Renders the Redis config into the tmpfs at /data and starts the server with it,
# so the password never appears on a command line. Cache only: no persistence,
# bounded memory, least recently used keys evicted. Per-service ACL users arrive
# with the services (slice 4). Compose invokes this script through `sh`.
set -eu

: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
case "$REDIS_PASSWORD" in
    *[[:space:]]* | *'"'* | *'\'*)
        echo "error: REDIS_PASSWORD must not contain spaces, quotes or backslashes" >&2
        exit 1
        ;;
esac

umask 077
cat > /data/redis.conf <<EOF
requirepass $REDIS_PASSWORD
save ""
appendonly no
maxmemory 64mb
maxmemory-policy allkeys-lru
EOF

exec redis-server /data/redis.conf
