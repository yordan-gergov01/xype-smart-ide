"""Helpers for the index-store integration tests: app-role sessions over TLS and
registered repositories to write into."""

import uuid

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from common_lib.models import Repository, Team
from common_lib.models.connection import postgres_url
from common_lib.tls import client_context
from support.database import Database
from support.tls import ca_file


def app_sessions(database: Database, **server_settings: str) -> async_sessionmaker[AsyncSession]:
    """Sessions as the app role; `server_settings` are Postgres settings per connection."""
    engine = create_async_engine(
        postgres_url(database.app_settings()),
        connect_args={"ssl": client_context(ca_file()), "server_settings": server_settings},
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def register_repositories(
    sessions: async_sessionmaker[AsyncSession], count: int = 2
) -> list[uuid.UUID]:
    """`count` new repositories of one new team, never synced."""
    async with sessions.begin() as session:
        team = (
            await session.execute(insert(Team).values(name=uuid.uuid4().hex).returning(Team.id))
        ).scalar_one()
        return [
            (
                await session.execute(
                    insert(Repository)
                    .values(team_id=team, name=uuid.uuid4().hex, hmac_key_id="hk")
                    .returning(Repository.id)
                )
            ).scalar_one()
            for _ in range(count)
        ]
