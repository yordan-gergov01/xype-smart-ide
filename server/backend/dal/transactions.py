"""One transaction per store call, with driver errors mapped to typed errors that carry
no driver message (it may repeat statements or values). For stores added from slice 7b;
the older ones move here in a refactor commit (spec, carried forward from slice 7a)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common_lib.errors import DependencyUnavailableError, InvalidValueError


@asynccontextmanager
async def transaction(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """A CHECK, a foreign key or a value the column refuses is `InvalidValueError`; any
    other driver or network failure is `DependencyUnavailableError`."""
    try:
        async with sessions.begin() as session:
            yield session
    except (IntegrityError, DataError):
        raise InvalidValueError("the database refused the data") from None
    except (SQLAlchemyError, OSError):
        raise DependencyUnavailableError("database") from None
