"""Clients for the backend's stores. Created once in the composition root; none of
them connects until first used, so the API starts even while a store is down."""

from redis.asyncio import Redis
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.core.settings import BackendSettings


def create_database_engine(settings: BackendSettings) -> AsyncEngine:
    """SQLAlchemy async engine on asyncpg with a bounded pool."""
    url = URL.create(
        "postgresql+asyncpg",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )
    return create_async_engine(
        url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={"timeout": settings.readiness_timeout_seconds},
    )


def create_redis_client(settings: BackendSettings) -> Redis:
    """Redis client with this service's own ACL user and bounded timeouts."""
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        username=settings.redis_user,
        password=settings.redis_password.get_secret_value(),
        socket_timeout=settings.readiness_timeout_seconds,
        socket_connect_timeout=settings.readiness_timeout_seconds,
    )
