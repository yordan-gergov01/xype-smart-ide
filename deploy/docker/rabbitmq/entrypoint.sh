#!/bin/sh
# Renders the broker definitions (vhost, users, permissions and the message
# topology) from environment variables into a tmpfs, creates the Erlang cookie, then
# hands over to the image's own entrypoint. Compose invokes this script through `sh`.
# Passwords exist only in the environment and the tmpfs, never in the repository or
# the image.
#
# The topology is declared here, not by the services: no service has configure
# rights. Each index queue (ADR-0015) has its own exchange, so a user may publish only
# where its role needs: the backend to index.chunks, the indexer to index.vectors.
# The consumer of a queue may also publish to its retry and dead-letter exchanges:
# a retry waits in index.<q>.retry.<n> (about 1, 2 and 4 minutes, ADR-0004) and goes
# back to index.<q> when its TTL ends; after the last retry, or with a fixed reason,
# a message goes to index.<q>.dlq, which keeps it for 24 hours (the life of the keys
# that could decrypt it) and drops the oldest when full. The main and retry queues
# refuse publishes when full, so producers are pushed back instead of the disk.
set -eu

# Values are written into JSON strings verbatim, so anything that would need
# escaping is rejected with a clear message instead of a broken file.
require() {
    eval "value=\${$1:-}"
    if [ -z "$value" ]; then
        echo "error: environment variable $1 must be set" >&2
        exit 1
    fi
    case "$value" in
        *'"'* | *'\'* | *[[:cntrl:]]*)
            echo "error: $1 contains a double quote, backslash or control character" >&2
            exit 1
            ;;
    esac
}

for name in RABBITMQ_VHOST RABBITMQ_BACKEND_USER RABBITMQ_BACKEND_PASSWORD \
    RABBITMQ_INDEXER_USER RABBITMQ_INDEXER_PASSWORD; do
    require "$name"
done
for file in tls.crt tls.key ca.crt; do
    if [ ! -r "/certs/$file" ]; then
        echo "error: /certs/$file is missing; create the dev CA (docs/runbooks/local-dev.md)" >&2
        exit 1
    fi
done

# Retry delays in milliseconds (tests shorten them; at least 100 ms, so a mistake
# cannot bring back a fast retry storm).
RETRY_1_MS=${RABBITMQ_RETRY_1_MS:-60000}
RETRY_2_MS=${RABBITMQ_RETRY_2_MS:-120000}
RETRY_3_MS=${RABBITMQ_RETRY_3_MS:-240000}
for value in "$RETRY_1_MS" "$RETRY_2_MS" "$RETRY_3_MS"; do
    case "$value" in
        '' | *[!0-9]*)
            echo "error: RABBITMQ_RETRY_*_MS must be whole milliseconds" >&2
            exit 1
            ;;
    esac
    if [ "$value" -lt 100 ]; then
        echo "error: RABBITMQ_RETRY_*_MS must be at least 100" >&2
        exit 1
    fi
done

# One queue family: $1 is chunks or vectors, $2 the queue's byte limit.
exchanges() {
    for exchange in "xype.index.$1" "xype.index.$1.retry" "xype.index.$1.dlx"; do
        printf '    {"name": "%s", "vhost": "%s", "type": "direct", "durable": true,\n' \
            "$exchange" "$RABBITMQ_VHOST"
        printf '     "auto_delete": false, "internal": false, "arguments": {}},\n'
    done
}
queues() {
    printf '    {"name": "index.%s", "vhost": "%s", "durable": true, "auto_delete": false,\n' \
        "$1" "$RABBITMQ_VHOST"
    printf '     "arguments": {"x-queue-type": "quorum", "x-dead-letter-exchange": "xype.index.%s.dlx",\n' "$1"
    printf '                   "x-delivery-limit": 5, "x-max-length-bytes": %s,\n' "$2"
    printf '                   "x-overflow": "reject-publish"}},\n'
    n=1
    for ttl in "$RETRY_1_MS" "$RETRY_2_MS" "$RETRY_3_MS"; do
        printf '    {"name": "index.%s.retry.%s", "vhost": "%s", "durable": true, "auto_delete": false,\n' \
            "$1" "$n" "$RABBITMQ_VHOST"
        printf '     "arguments": {"x-queue-type": "quorum", "x-message-ttl": %s,\n' "$ttl"
        printf '                   "x-dead-letter-exchange": "xype.index.%s",\n' "$1"
        printf '                   "x-dead-letter-routing-key": "index.%s",\n' "$1"
        # A retry going back to a full queue waits, not vanishes (needs reject-publish).
        printf '                   "x-dead-letter-strategy": "at-least-once",\n'
        printf '                   "x-max-length-bytes": %s, "x-overflow": "reject-publish"}},\n' "$2"
        n=$((n + 1))
    done
    printf '    {"name": "index.%s.dlq", "vhost": "%s", "durable": true, "auto_delete": false,\n' \
        "$1" "$RABBITMQ_VHOST"
    printf '     "arguments": {"x-queue-type": "quorum", "x-message-ttl": 86400000,\n'
    printf '                   "x-max-length-bytes": 67108864, "x-overflow": "drop-head"}},\n'
}
bindings() {
    binding() {
        printf '    {"source": "%s", "vhost": "%s", "destination": "%s",\n' "$1" "$RABBITMQ_VHOST" "$2"
        printf '     "destination_type": "queue", "routing_key": "%s", "arguments": {}},\n' "$3"
    }
    binding "xype.index.$1" "index.$1" "index.$1"
    for n in 1 2 3; do
        binding "xype.index.$1.retry" "index.$1.retry.$n" "index.$1.retry.$n"
    done
    binding "xype.index.$1.dlx" "index.$1.dlq" "index.$1"
}
umask 077
{
    cat <<EOF
{
  "vhosts": [
    {"name": "$RABBITMQ_VHOST", "metadata": {"default_queue_type": "quorum"}}
  ],
  "users": [
    {"name": "$RABBITMQ_BACKEND_USER", "password": "$RABBITMQ_BACKEND_PASSWORD", "tags": []},
    {"name": "$RABBITMQ_INDEXER_USER", "password": "$RABBITMQ_INDEXER_PASSWORD", "tags": []}
  ],
  "permissions": [
    {"user": "$RABBITMQ_BACKEND_USER", "vhost": "$RABBITMQ_VHOST", "configure": "^$",
     "write": "^xype\\\\.index\\\\.(chunks|vectors\\\\.retry|vectors\\\\.dlx)$", "read": "^index\\\\.vectors$"},
    {"user": "$RABBITMQ_INDEXER_USER", "vhost": "$RABBITMQ_VHOST", "configure": "^$",
     "write": "^xype\\\\.index\\\\.(vectors|chunks\\\\.retry|chunks\\\\.dlx)$", "read": "^index\\\\.chunks$"}
  ],
  "exchanges": [
EOF
    { exchanges chunks; exchanges vectors; } | sed '$ s/,$//'
    printf '  ],\n  "queues": [\n'
    { queues chunks 268435456; queues vectors 134217728; } | sed '$ s/,$//'
    printf '  ],\n  "bindings": [\n'
    { bindings chunks; bindings vectors; } | sed '$ s/,$//'
    printf '  ]\n}\n'
} > /run/rabbitmq-definitions/definitions.json

# The container runs as the rabbitmq user, so the broker and the healthcheck's
# rabbitmq-diagnostics share one cookie; creating it first stops both racing.
cookie=/var/lib/rabbitmq/.erlang.cookie
if [ ! -f "$cookie" ]; then
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$cookie"
    chmod 400 "$cookie"
fi

exec docker-entrypoint.sh "$@"
