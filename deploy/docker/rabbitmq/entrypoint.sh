#!/bin/sh
# Creates the Erlang cookie before the broker starts, then hands over to the
# image's own entrypoint. Compose invokes this script through `sh`, so it does not
# depend on the executable bit.
#
# The container runs as the rabbitmq user, so the broker and the healthcheck's
# rabbitmq-diagnostics share one cookie; creating it first stops both racing to
# write it.
set -eu

cookie=/var/lib/rabbitmq/.erlang.cookie
if [ ! -f "$cookie" ]; then
    umask 077
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$cookie"
    chmod 400 "$cookie"
fi

exec docker-entrypoint.sh "$@"
