"""Clients for the backend's stores. Created once in the composition root; none of
them connects until first used, so the API starts even while a store is down."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.settings import BackendSettings
from common_lib.models.connection import postgres_url
from common_lib.tls import MINIMUM_TLS, client_context


@dataclass(frozen=True, slots=True)
class Database:
    """Write (primary) and read engines with their session factories (brief §3.8).

    Repositories take `write_sessions` for changes and for reads that must see them,
    and `read_sessions` for queries that tolerate replica lag.
    """

    write_engine: AsyncEngine
    read_engine: AsyncEngine
    write_sessions: async_sessionmaker[AsyncSession]
    read_sessions: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.write_engine.dispose()
        if self.read_engine is not self.write_engine:
            await self.read_engine.dispose()


def _engine(settings: BackendSettings, host: str) -> AsyncEngine:
    return create_async_engine(
        postgres_url(settings, host=host),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={
            "ssl": client_context(settings.tls_ca_file),
            "timeout": settings.db_connect_timeout_seconds,
            "command_timeout": settings.db_command_timeout_seconds,
        },
    )


def create_database(settings: BackendSettings) -> Database:
    write = _engine(settings, settings.db_host)
    read = _engine(settings, settings.db_read_host) if settings.db_read_host else write
    return Database(
        write_engine=write,
        read_engine=read,
        write_sessions=async_sessionmaker(write, expire_on_commit=False),
        read_sessions=async_sessionmaker(read, expire_on_commit=False),
    )


def create_redis_client(settings: BackendSettings) -> Redis:
    """Redis client with this service's own ACL user, TLS and bounded timeouts."""
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        username=settings.redis_user,
        password=settings.redis_password.get_secret_value(),
        ssl=True,
        ssl_ca_certs=str(settings.tls_ca_file),
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        ssl_min_version=MINIMUM_TLS,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
    )
