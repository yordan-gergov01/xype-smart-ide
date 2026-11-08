#!/bin/sh
# Runs once, on the first start with an empty data volume.
#
# Two roles, neither a superuser:
#   owner  owns the database and its schema; only the one-shot migrate service uses it.
#   app    the runtime role of the backend: read and write rows, never change the schema,
#          and no access to the `migrations` schema (Alembic's version table).
# Tables the owner creates later grant the app role data privileges automatically
# (default privileges). pgvector is enabled here by the superuser, so neither role
# needs superuser rights.
#
# The official entrypoint sources *.sh files that are not executable, so this
# script does not depend on the executable bit. Because it is sourced, it sets no
# shell options of its own: the entrypoint already runs with errexit, and
# ON_ERROR_STOP makes any SQL error fatal. Values come from the environment
# through \getenv, so no password is ever on a command line.

psql --no-psqlrc -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<'SQL'
-- A failing statement would otherwise be logged with its password literal.
SET log_min_error_statement = panic;
\getenv owner_user POSTGRES_OWNER_USER
\getenv owner_password POSTGRES_OWNER_PASSWORD
\getenv app_user POSTGRES_APP_USER
\getenv app_password POSTGRES_APP_PASSWORD
\getenv app_db POSTGRES_DB

CREATE ROLE :"owner_user" LOGIN PASSWORD :'owner_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

ALTER DATABASE :"app_db" OWNER TO :"owner_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"app_db" TO :"app_user";

ALTER SCHEMA public OWNER TO :"owner_user";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";

-- Alembic's version table lives here: the owner's only, no access for the app role.
CREATE SCHEMA migrations AUTHORIZATION :"owner_user";

CREATE EXTENSION IF NOT EXISTS vector;
SQL
