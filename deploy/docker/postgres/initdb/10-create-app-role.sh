#!/bin/sh
# Runs once, on the first start with an empty data volume.
#
# Creates the non-superuser role the server connects with, hands it ownership of
# the application database, and enables pgvector as the superuser so the
# application role never needs superuser rights.
#
# The official entrypoint sources *.sh files that are not executable, so this
# script does not depend on the executable bit. Because it is sourced, it sets no
# shell options of its own: the entrypoint already runs with errexit, and
# ON_ERROR_STOP makes any SQL error fatal. Values come from the environment
# through \getenv, so the password is never on a command line.

psql --no-psqlrc -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_user POSTGRES_APP_USER
\getenv app_password POSTGRES_APP_PASSWORD
\getenv app_db POSTGRES_DB
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER DATABASE :"app_db" OWNER TO :"app_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
