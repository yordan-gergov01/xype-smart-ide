"""The Alembic history builds the schema the models describe, with the role split of
deploy/docker/postgres/initdb: the owner migrates, the app role only reads and writes.

Sync tests on purpose: Alembic's env.py runs its own event loop.
"""

import asyncio
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import URL, Connection, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from common_lib.models import Base
from common_lib.models.connection import postgres_url
from common_lib.settings import PostgresSettings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "server"
INITDB = ROOT / "deploy" / "docker" / "postgres" / "initdb"
IMAGE = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text("utf-8"))["services"][
    "postgres"
]["image"]
ROLES = {
    "POSTGRES_OWNER_USER": "xype_owner",
    "POSTGRES_OWNER_PASSWORD": "test-only-owner",
    "POSTGRES_APP_USER": "xype_app",
    "POSTGRES_APP_PASSWORD": "test-only-app",
}


@pytest.fixture(scope="module")
def database() -> Iterator[dict[str, Any]]:
    container = PostgresContainer(
        IMAGE, username="postgres", password="test-only-super", dbname="xype", driver=None
    ).with_volume_mapping(str(INITDB), "/docker-entrypoint-initdb.d", "ro")
    for name, value in ROLES.items():
        container = container.with_env(name, value)
    with container:
        yield {
            "host": container.get_container_host_ip(),
            "port": int(container.get_exposed_port(5432)),
        }


def _url(database: dict[str, Any], user: str, password: str) -> URL:
    settings = PostgresSettings(
        db_host=database["host"],
        db_port=database["port"],
        db_name="xype",
        db_user=user,
        db_password=SecretStr(password),
    )
    return postgres_url(settings)


def _alembic(database: dict[str, Any], *commands: tuple[str, str]) -> None:
    env = {
        "DB_HOST": database["host"],
        "DB_PORT": str(database["port"]),
        "DB_NAME": "xype",
        "DB_USER": ROLES["POSTGRES_OWNER_USER"],
        "DB_PASSWORD": ROLES["POSTGRES_OWNER_PASSWORD"],
        "LOG_LEVEL": "WARNING",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        config = Config(str(SERVER / "alembic.ini"))
        for name, target in commands:
            getattr(command, name)(config, target)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _with_connection(url: URL, action: Any) -> Any:  # reason: any sync callback
    async def run() -> Any:  # reason: returns the callback's result
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                result = await connection.run_sync(action)
                await connection.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _diff(connection: Connection) -> list[Any]:
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


def test_upgrade_builds_exactly_the_model_schema(database: dict[str, Any]) -> None:
    _alembic(database, ("upgrade", "head"))
    owner = _url(database, ROLES["POSTGRES_OWNER_USER"], ROLES["POSTGRES_OWNER_PASSWORD"])
    assert _with_connection(owner, _diff) == []


def test_app_role_reads_and_writes_rows_but_cannot_change_the_schema(
    database: dict[str, Any],
) -> None:
    _alembic(database, ("upgrade", "head"))
    app = _url(database, ROLES["POSTGRES_APP_USER"], ROLES["POSTGRES_APP_PASSWORD"])

    def write_and_read(connection: Connection) -> int:
        team = connection.execute(
            text("INSERT INTO teams (name) VALUES (gen_random_uuid()::text) RETURNING id")
        ).scalar_one()
        user = connection.execute(
            text(
                "INSERT INTO users (team_id, email, display_name, client_id, client_secret_hash)"
                " VALUES (:t, gen_random_uuid()::text, 'u', gen_random_uuid()::text, 'h')"
                " RETURNING id"
            ),
            {"t": team},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO llm_models (id, name, provider, model_version, max_context_length)"
                " VALUES ('mock', 'Mock', 'mock', '1', 4096) ON CONFLICT DO NOTHING"
            )
        )
        # BIGSERIAL: needs the sequence privileges from the default privileges.
        return int(
            connection.execute(
                text(
                    "INSERT INTO cost_ledger (user_id, model_id, task_type, request_id, is_peak)"
                    " VALUES (:u, 'mock', 'chat', 'r', false) RETURNING id"
                ),
                {"u": user},
            ).scalar_one()
        )

    assert _with_connection(app, write_and_read) >= 1
    for ddl in (
        "CREATE TABLE intruder (id int)",
        "CREATE SCHEMA intruder",
        "DROP TABLE teams",
        "TRUNCATE teams",
        "CREATE TRIGGER t BEFORE INSERT ON teams FOR EACH ROW EXECUTE FUNCTION now()",
        "UPDATE migrations.alembic_version SET version_num = 'x'",
        "SELECT * FROM migrations.alembic_version",
    ):
        with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
            _with_connection(app, lambda c, sql=ddl: c.execute(text(sql)))


def test_checks_reject_plaintext_ids_and_credentials_in_urls(database: dict[str, Any]) -> None:
    _alembic(database, ("upgrade", "head"))
    app = _url(database, ROLES["POSTGRES_APP_USER"], ROLES["POSTGRES_APP_PASSWORD"])

    def insert(sql: str) -> Callable[[Connection], Any]:
        def run(connection: Connection) -> Any:  # reason: any statement result
            team = connection.execute(
                text("INSERT INTO teams (name) VALUES (gen_random_uuid()::text) RETURNING id")
            ).scalar_one()
            return connection.execute(text(sql), {"t": team})

        return run

    for sql in (
        "INSERT INTO repositories (team_id, name, hmac_key_id, merkle_root_hash)"
        " VALUES (:t, 'r', 'k', 'src/billing/invoice.py')",
        "INSERT INTO repositories (team_id, name, hmac_key_id, remote_url)"
        " VALUES (:t, 'r', 'k', 'https://user:token@git.example.com/x.git')",
    ):
        with pytest.raises(IntegrityError, match="violates check constraint"):
            _with_connection(app, insert(sql))
    ok = (
        "INSERT INTO repositories (team_id, name, hmac_key_id, remote_url, merkle_root_hash)"
        " VALUES (:t, 'r', 'k', 'https://git.example.com/x.git', repeat('a', 64))"
    )
    _with_connection(app, insert(ok))


def test_downgrade_and_upgrade_again(database: dict[str, Any]) -> None:
    _alembic(database, ("downgrade", "base"), ("upgrade", "head"))
    owner = _url(database, ROLES["POSTGRES_OWNER_USER"], ROLES["POSTGRES_OWNER_PASSWORD"])
    assert _with_connection(owner, _diff) == []
