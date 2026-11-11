"""Refresh tokens in Postgres (`RefreshTokenStore`, ADR-0014): stored by peppered hash,
rotated atomically, and revoked by family on reuse."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.dal.auth.session import unit_of_work
from common_lib.auth import (
    ROLES,
    NewRefreshToken,
    NotFound,
    Principal,
    Reused,
    Rotated,
    Rotation,
)
from common_lib.models import RefreshToken, User


class SqlRefreshTokenStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, token: NewRefreshToken, *, secret_hash: str) -> bool:
        async with unit_of_work(self._sessions) as session:
            # The per-user lock of `rotate` and `revoke_user`, and a re-check under it.
            current = await session.execute(
                select(User.id)
                .where(
                    User.id == token.user_id,
                    User.is_active,
                    User.client_secret_hash == secret_hash,
                )
                .with_for_update()
            )
            if current.first() is None:
                return False
            await session.execute(
                insert(RefreshToken).values(
                    user_id=token.user_id,
                    family_id=token.family_id,
                    token_hash=token.token_hash,
                    expires_at=token.expires_at,
                    family_expires_at=token.family_expires_at,
                )
            )
        return True

    async def rotate(
        self,
        token_hashes: Sequence[str],
        new_token_hash: str,
        *,
        now: datetime,
        refresh_ttl_seconds: int,
    ) -> Rotation:
        async with unit_of_work(self._sessions) as session:
            if not await self._lock_owner(session, token_hashes):
                return NotFound()
            revoked = (
                await session.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.token_hash.in_(token_hashes),
                        RefreshToken.revoked_at.is_(None),
                        RefreshToken.expires_at > now,
                        RefreshToken.family_expires_at > now,
                        User.id == RefreshToken.user_id,
                        User.is_active,
                    )
                    .values(revoked_at=now)
                    .returning(
                        RefreshToken.user_id,
                        RefreshToken.family_id,
                        RefreshToken.family_expires_at,
                        User.team_id,
                        User.role,
                    )
                )
            ).one_or_none()
            if revoked is None:
                return await self._reuse_or_not_found(session, token_hashes, now)
            await session.execute(
                insert(RefreshToken).values(
                    user_id=revoked.user_id,
                    family_id=revoked.family_id,
                    token_hash=new_token_hash,
                    expires_at=min(
                        now + timedelta(seconds=refresh_ttl_seconds), revoked.family_expires_at
                    ),
                    family_expires_at=revoked.family_expires_at,
                )
            )
        principal = Principal(revoked.user_id, revoked.team_id, ROLES[revoked.role])
        return Rotated(principal, revoked.family_id)

    @staticmethod
    async def _lock_owner(session: AsyncSession, token_hashes: Sequence[str]) -> bool:
        """Lock the token owner's `users` row for the rest of the transaction.

        Every rotation and revocation of a user's tokens takes this lock first, so they
        run one after another, and each statement after it sees the others' committed
        rows. Without it, revoking a family could miss the token a concurrent rotation
        is just inserting, and reuse detection could be bypassed. Refreshes are rare (one
        per window every 15 minutes), so serialising them per user costs nothing.
        """
        owner = await session.execute(
            select(User.id)
            .join(RefreshToken, RefreshToken.user_id == User.id)
            .where(RefreshToken.token_hash.in_(token_hashes))
            .with_for_update(of=User)
        )
        return owner.first() is not None

    async def _reuse_or_not_found(
        self, session: AsyncSession, token_hashes: Sequence[str], now: datetime
    ) -> Rotation:
        presented = (
            await session.execute(
                select(RefreshToken.user_id, RefreshToken.family_id, RefreshToken.revoked_at).where(
                    RefreshToken.token_hash.in_(token_hashes)
                )
            )
        ).one_or_none()
        if presented is None or presented.revoked_at is None:
            return NotFound()
        await self._revoke(session, RefreshToken.family_id == presented.family_id, now)
        return Reused(presented.user_id, presented.family_id)

    async def revoke_user(self, user_id: uuid.UUID, *, now: datetime) -> None:
        async with unit_of_work(self._sessions) as session:
            # The same lock as `rotate`: a rotation in flight finishes first, and the
            # token it inserts is revoked here too.
            await session.execute(select(User.id).where(User.id == user_id).with_for_update())
            await self._revoke(session, RefreshToken.user_id == user_id, now)

    @staticmethod
    async def _revoke(session: AsyncSession, scope: ColumnElement[bool], now: datetime) -> None:
        await session.execute(
            update(RefreshToken)
            .where(scope, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
