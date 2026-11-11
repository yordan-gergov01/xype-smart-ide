#!/bin/sh
# Renders the broker definitions (vhost, users, permissions) from environment
# variables into a tmpfs, creates the Erlang cookie, then hands over to the image's
# own entrypoint. Compose invokes this script through `sh`. Passwords exist only in
# the environment and the tmpfs, never in the repository or the image.
#
# Permissions follow the message topology. Until it exists (Phase 5), the backend
# may only connect to the vhost: every permission regex is empty.
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

for name in RABBITMQ_VHOST RABBITMQ_BACKEND_USER RABBITMQ_BACKEND_PASSWORD; do
    require "$name"
done
for file in tls.crt tls.key ca.crt; do
    if [ ! -r "/certs/$file" ]; then
        echo "error: /certs/$file is missing; create the dev CA (docs/runbooks/local-dev.md)" >&2
        exit 1
    fi
done

umask 077
cat > /run/rabbitmq-definitions/definitions.json <<EOF
{
  "vhosts": [
    {"name": "$RABBITMQ_VHOST", "metadata": {"default_queue_type": "quorum"}}
  ],
  "users": [
    {"name": "$RABBITMQ_BACKEND_USER", "password": "$RABBITMQ_BACKEND_PASSWORD", "tags": []}
  ],
  "permissions": [
    {"user": "$RABBITMQ_BACKEND_USER", "vhost": "$RABBITMQ_VHOST",
     "configure": "^$", "write": "^$", "read": "^$"}
  ]
}
EOF

# The container runs as the rabbitmq user, so the broker and the healthcheck's
# rabbitmq-diagnostics share one cookie; creating it first stops both racing.
cookie=/var/lib/rabbitmq/.erlang.cookie
if [ ! -f "$cookie" ]; then
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$cookie"
    chmod 400 "$cookie"
fi

exec docker-entrypoint.sh "$@"
