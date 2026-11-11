"""Client credentials, managed by an admin (ADR-0014, Q11): the CLI now, the admin
dashboard (Phase 7) later, both through this service."""

import uuid
from datetime import timedelta

import structlog

from backend.services.auth.access_tokens import CLOCK_SKEW_SECONDS
from backend.services.auth.peppers import PepperRing, new_client_id, new_client_secret
from backend.services.auth.ports import RefreshTokenStore, RevocationPublisher, UserStore
from common_lib.auth import AuthError, NewClient, Revocation, Role
from common_lib.clock import Clock
from common_lib.security import KeyStore

logger = structlog.get_logger(__name__)


class UnknownClientError(AuthError):
    """No user has this client id, or no team has this name."""


class ClientAdmin:
    def __init__(
        self,
        users: UserStore,
        tokens: RefreshTokenStore,
        revocations: RevocationPublisher,
        keys: KeyStore,
        *,
        peppers: PepperRing,
        clock: Clock,
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._revocations = revocations
        self._keys = keys
        self._peppers = peppers
        self._clock = clock

    async def create_team(self, name: str) -> uuid.UUID:
        team_id = await self._users.create_team(name)
        logger.info("auth.team.created", team_id=str(team_id))
        return team_id

    async def create_client(
        self, *, team: str, email: str, display_name: str, role: Role = "developer"
    ) -> NewClient:
        """A new user with fresh client credentials. The secret is returned once."""
        team_id = await self._users.find_team(team)
        if team_id is None:
            raise UnknownClientError("unknown team")
        client_id, secret = new_client_id(), new_client_secret()
        user_id = await self._users.create_user(
            team_id=team_id,
            email=email,
            display_name=display_name,
            role=role,
            client_id=client_id,
            secret_hash=self._peppers.secret_hash(secret.encode()),
        )
        logger.info("auth.client.created", user_id=str(user_id), team_id=str(team_id))
        return NewClient(user_id, client_id, secret)

    async def disable_client(self, client_id: str) -> uuid.UUID:
        """The user can no longer log in or refresh, and every replica closes their
        streams. Access tokens already issued stay valid until they expire (≤ 15 min)."""
        user_id = await self._users.set_active(client_id, active=False)
        if user_id is None:
            raise UnknownClientError("unknown client id")
        await self._revoke(user_id)
        logger.info("auth.client.disabled", user_id=str(user_id))
        return user_id

    async def rotate_secret(self, client_id: str) -> NewClient:
        """A new secret; the old one and every refresh token stop working."""
        secret = new_client_secret()
        user_id = await self._users.set_secret_hash(
            client_id, self._peppers.secret_hash(secret.encode())
        )
        if user_id is None:
            raise UnknownClientError("unknown client id")
        await self._revoke(user_id)
        logger.info("auth.client.secret_rotated", user_id=str(user_id))
        return NewClient(user_id, client_id, secret)

    async def _revoke(self, user_id: uuid.UUID) -> None:
        """Refresh tokens, then session keys, then the replicas. Each step is idempotent:
        if one fails (503), running the command again completes the rest.

        The key store refuses keys for access tokens issued up to the clock skew after
        the refresh tokens were revoked: a login or refresh in flight signs its token
        after that, and the gateway's clock may run ahead of this one. A new login in
        those 30 s gets 401 on `/session-key` and retries."""
        await self._tokens.revoke_user(user_id, now=self._clock.now())
        refuse_until = self._clock.now() + timedelta(seconds=CLOCK_SKEW_SECONDS)
        await self._keys.revoke_user(str(user_id), refuse_issued_until=refuse_until)
        await self._revocations.publish(Revocation("user", user_id))
