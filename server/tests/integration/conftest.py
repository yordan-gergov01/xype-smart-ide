"""Fixtures shared by the index-store integration tests (`VectorStore`, `MerkleIndex`):
one migrated Postgres with pgvector per module, sessions as the app role over TLS, and
helpers in `support/stores.py`."""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from support.database import Database, alembic, postgres
from support.stores import app_sessions


@pytest.fixture(scope="module")
def migrated_database() -> Iterator[Database]:
    with postgres() as db:
        alembic(db, ("upgrade", "head"))
        yield db


@pytest.fixture
async def index_sessions(
    migrated_database: Database,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sessions = app_sessions(migrated_database)
    yield sessions
    await sessions.kw["bind"].dispose()
