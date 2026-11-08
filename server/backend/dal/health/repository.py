"""Reachability checks for the stores the backend owns.

Driver errors are translated here, at the adapter boundary, into
`DependencyUnavailableError` without their messages.
"""

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from common_lib.errors import DependencyUnavailableError


class DatabaseProbe:
    """Postgres answers `SELECT 1` through the connection pool."""

    def __init__(self, engine: AsyncEngine, *, name: str = "database") -> None:
        self._engine = engine
        self.name = name

    async def check(self) -> None:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            raise DependencyUnavailableError(self.name) from None


class RedisProbe:
    """Redis answers PING with this service's credentials."""

    name = "redis"

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def check(self) -> None:
        try:
            await self._client.ping()
        except (RedisError, OSError):
            raise DependencyUnavailableError(self.name) from None
