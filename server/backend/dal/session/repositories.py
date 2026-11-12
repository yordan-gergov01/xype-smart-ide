"""`RepositoryAccess` in Postgres: which registered repositories belong to a team."""

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common_lib.errors import DependencyUnavailableError
from common_lib.models import Repository


class SqlRepositoryAccess:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        # Read sessions: a repository registered moments ago may lag on a replica; the
        # client retries its Hello.
        self._sessions = sessions

    async def accessible(
        self, team_id: uuid.UUID, repository_ids: Collection[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        if not repository_ids:
            return frozenset()
        query = select(Repository.id).where(
            Repository.team_id == team_id, Repository.id.in_(list(repository_ids))
        )
        try:
            async with self._sessions() as session:
                return frozenset((await session.execute(query)).scalars())
        except (SQLAlchemyError, OSError):
            raise DependencyUnavailableError("database") from None
