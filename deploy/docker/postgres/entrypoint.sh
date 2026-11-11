#!/bin/sh
# Copies the TLS key from the read-only /certs mount into the tmpfs with mode 0600,
# then hands over to the image's own entrypoint. Postgres refuses a key file that
# others can read, and bind mounts (Docker Desktop) show every file as world-readable.
# Compose invokes this script through `sh`; certificates come from the dev CA
# (ADR-0036, docs/runbooks/local-dev.md).
set -eu

for file in tls.crt tls.key; do
    if [ ! -r "/certs/$file" ]; then
        echo "error: /certs/$file is missing; create the dev CA (docs/runbooks/local-dev.md)" >&2
        exit 1
    fi
done

umask 077
cp /certs/tls.key /var/run/postgresql/tls.key

exec docker-entrypoint.sh "$@"
