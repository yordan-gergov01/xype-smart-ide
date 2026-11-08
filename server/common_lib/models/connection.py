"""The one place that builds a Postgres URL (backend engines, migrations, tests)."""

from sqlalchemy import URL

from common_lib.settings import PostgresSettings


def postgres_url(settings: PostgresSettings, *, host: str | None = None) -> URL:
    """asyncpg URL for `settings`; `host` overrides the primary (read replica)."""
    return URL.create(
        "postgresql+asyncpg",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=host or settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )
