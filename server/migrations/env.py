"""Alembic environment: migrates with the schema owner role (ADR-0035).

Run by the one-shot `migrate` service (`alembic upgrade head`) before the backend
starts. Connection settings come from the environment only (ADR-0010); the runtime
role the backend uses has data privileges only and cannot change the schema.
"""

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from common_lib.logging_config import configure_logging
from common_lib.models import Base
from common_lib.models.connection import postgres_url
from common_lib.settings import PostgresSettings, load_settings
from common_lib.tls import client_context


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        transaction_per_migration=True,
        # Owned by the schema owner, invisible to the runtime role, which therefore
        # cannot rewrite the migration state.
        version_table_schema="migrations",
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _migrate(settings: PostgresSettings) -> None:
    engine = create_async_engine(
        postgres_url(settings), connect_args={"ssl": client_context(settings.tls_ca_file)}
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
            await connection.commit()
    finally:
        await engine.dispose()


def main() -> None:
    # DB_USER is the schema owner here, not the runtime role.
    settings = load_settings(PostgresSettings)
    configure_logging(service="migrate", level=settings.log_level, log_format=settings.log_format)
    if context.is_offline_mode():
        raise SystemExit("Offline SQL generation is not used; run against a database.")
    asyncio.run(_migrate(settings))


main()
