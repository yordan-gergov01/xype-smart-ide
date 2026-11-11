"""One transaction per store call, with driver errors mapped to typed errors."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common_lib.auth import ConflictError
from common_lib.errors import DependencyUnavailableError


@asynccontextmanager
async def unit_of_work(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One transaction. Driver errors become typed errors without their messages, which
    may repeat statements or values."""
    try:
        async with sessions.begin() as session:
            yield session
    except IntegrityError:
        raise ConflictError("already exists") from None
    except (SQLAlchemyError, OSError):
        raise DependencyUnavailableError("database") from None
