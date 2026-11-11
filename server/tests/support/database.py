"""A TLS Postgres container with the compose file's roles, and Alembic against it.

Alembic's env.py runs its own event loop, so `alembic` is called from sync code only
(module fixtures, sync tests).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import URL

from common_lib.models.connection import postgres_url
from common_lib.settings import PostgresSettings
from support import containers
from support.tls import ca_file

SERVER = Path(__file__).resolve().parents[2]
ROLES = {
    "POSTGRES_OWNER_USER": "xype_owner",
    "POSTGRES_OWNER_PASSWORD": "test-only-owner",
    "POSTGRES_APP_USER": "xype_app",
    "POSTGRES_APP_PASSWORD": "test-only-app",
}


@dataclass(frozen=True, slots=True)
class Database:
    host: str
    port: int

    def settings(self, user: str, password: str) -> PostgresSettings:
        return PostgresSettings(
            db_host=self.host,
            db_port=self.port,
            db_name="xype",
            db_user=user,
            db_password=SecretStr(password),
            tls_ca_file=ca_file(),
        )

    def url(self, user: str, password: str) -> URL:
        return postgres_url(self.settings(user, password))

    def app_settings(self) -> PostgresSettings:
        return self.settings(ROLES["POSTGRES_APP_USER"], ROLES["POSTGRES_APP_PASSWORD"])


@contextmanager
def postgres() -> Iterator[Database]:
    """Postgres with the owner and app roles of deploy/docker/postgres/initdb."""
    env = {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "test-only-super",
        "POSTGRES_DB": "xype",
        **ROLES,
    }
    with containers.postgres(env, initdb=True) as container:
        yield Database(container.get_container_host_ip(), int(container.get_exposed_port(5432)))


def alembic(database: Database, *commands: tuple[str, str]) -> None:
    """Runs Alembic commands as the schema owner, as the migrate service does."""
    env = {
        "DB_HOST": database.host,
        "DB_PORT": str(database.port),
        "DB_NAME": "xype",
        "DB_USER": ROLES["POSTGRES_OWNER_USER"],
        "DB_PASSWORD": ROLES["POSTGRES_OWNER_PASSWORD"],
        "LOG_LEVEL": "WARNING",
        "TLS_CA_FILE": str(ca_file()),
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
