"""`RepositoryStore` and `RepositoryKeySource` in Postgres: registration writes a
repository and its key in one transaction; the session-key exchange reads the active
keys of the caller's team's repositories in one query."""

import uuid
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common_lib.errors import DependencyUnavailableError, InvalidValueError
from common_lib.models import Repository, RepositoryKey
from common_lib.repositories import DuplicateRepositoryError, NewRepository, StoredRepositoryKey
from common_lib.security import WrappedKey

_NONCE_BYTES = 12
_UNIQUE_VIOLATION = "23505"


@asynccontextmanager
async def _transaction(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Driver errors become typed errors without their messages, which may repeat values.
    A unique violation is a taken name; any other integrity error (a CHECK, a team that
    no longer exists) is invalid data."""
    try:
        async with sessions.begin() as session:
            yield session
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
            raise DuplicateRepositoryError("already exists") from None
        raise InvalidValueError("invalid repository data") from None
    except (SQLAlchemyError, OSError):
        raise DependencyUnavailableError("database") from None


class SqlRepositoryStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        # Write sessions for both: a key read on a lagging replica right after
        # registration would look exactly like "not your repository" to the client.
        self._sessions = sessions

    async def create(self, repository: NewRepository) -> None:
        """Raises `DuplicateRepositoryError` when the team already has a repository of this
        name, and `InvalidValueError` when a CHECK or foreign key refuses the row."""
        wrapped = repository.wrapped_key
        async with _transaction(self._sessions) as session:
            await session.execute(
                insert(Repository).values(
                    id=repository.repository_id,
                    team_id=repository.team_id,
                    name=repository.name,
                    remote_url=repository.remote_url,
                    is_template=repository.is_template,
                    hmac_key_id=repository.hmac_key_id,
                )
            )
            await session.execute(
                insert(RepositoryKey).values(
                    repository_id=repository.repository_id,
                    hmac_key_id=repository.hmac_key_id,
                    kek_id=wrapped.kek_id,
                    wrapped_key_enc=wrapped.nonce + wrapped.ciphertext,
                )
            )

    async def active_keys(
        self, team_id: uuid.UUID, repository_ids: Collection[uuid.UUID]
    ) -> Sequence[StoredRepositoryKey]:
        if not repository_ids:
            return ()
        query = (
            select(
                RepositoryKey.repository_id,
                RepositoryKey.hmac_key_id,
                RepositoryKey.kek_id,
                RepositoryKey.wrapped_key_enc,
            )
            .join(Repository, Repository.id == RepositoryKey.repository_id)
            .where(
                Repository.team_id == team_id,
                Repository.id.in_(list(repository_ids)),
                RepositoryKey.retired_at.is_(None),
            )
            .order_by(RepositoryKey.repository_id)
        )
        async with _transaction(self._sessions) as session:
            rows = (await session.execute(query)).all()
        return tuple(
            StoredRepositoryKey(
                row.repository_id,
                row.hmac_key_id,
                WrappedKey(
                    row.kek_id,
                    bytes(row.wrapped_key_enc[:_NONCE_BYTES]),
                    bytes(row.wrapped_key_enc[_NONCE_BYTES:]),
                ),
            )
            for row in rows
        )
