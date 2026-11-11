"""Users and teams in Postgres (`UserStore`, ADR-0014)."""

import uuid
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.dal.auth.session import unit_of_work
from common_lib.auth import ROLES, Principal, Role, StoredCredentials
from common_lib.models import Team, User


class SqlUserStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def find_credentials(self, client_id: str) -> StoredCredentials | None:
        query = select(
            User.id, User.team_id, User.role, User.client_secret_hash, User.is_active
        ).where(User.client_id == client_id)
        async with unit_of_work(self._sessions) as session:
            row = (await session.execute(query)).one_or_none()
        if row is None:
            return None
        principal = Principal(user_id=row.id, team_id=row.team_id, role=ROLES[row.role])
        return StoredCredentials(principal, row.client_secret_hash, row.is_active)

    async def record_login(self, user_id: uuid.UUID, at: datetime) -> None:
        async with unit_of_work(self._sessions) as session:
            await session.execute(update(User).where(User.id == user_id).values(last_login=at))

    async def create_team(self, name: str) -> uuid.UUID:
        async with unit_of_work(self._sessions) as session:
            return (
                await session.execute(insert(Team).values(name=name).returning(Team.id))
            ).scalar_one()

    async def find_team(self, name: str) -> uuid.UUID | None:
        async with unit_of_work(self._sessions) as session:
            result = await session.execute(select(Team.id).where(Team.name == name))
            return result.scalar_one_or_none()

    async def create_user(
        self,
        *,
        team_id: uuid.UUID,
        email: str,
        display_name: str,
        role: Role,
        client_id: str,
        secret_hash: str,
    ) -> uuid.UUID:
        values = {
            "team_id": team_id,
            "email": email,
            "display_name": display_name,
            "role": role,
            "client_id": client_id,
            "client_secret_hash": secret_hash,
        }
        async with unit_of_work(self._sessions) as session:
            result = await session.execute(insert(User).values(**values).returning(User.id))
            return result.scalar_one()

    async def set_active(self, client_id: str, *, active: bool) -> uuid.UUID | None:
        return await self._update(client_id, is_active=active)

    async def set_secret_hash(self, client_id: str, secret_hash: str) -> uuid.UUID | None:
        return await self._update(client_id, client_secret_hash=secret_hash)

    async def _update(self, client_id: str, **values: object) -> uuid.UUID | None:
        statement = (
            update(User).where(User.client_id == client_id).values(**values).returning(User.id)
        )
        async with unit_of_work(self._sessions) as session:
            return (await session.execute(statement)).scalar_one_or_none()
