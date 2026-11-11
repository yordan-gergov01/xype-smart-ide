"""The auth feature's seams. Adapters live in `backend.dal.auth`; fakes and shared
contract suites in tests/support (rule 03)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from common_lib.auth import (
    Credential,
    NewRefreshToken,
    RateDecision,
    Revocation,
    Role,
    Rotation,
    StoredCredentials,
    VerifiedCredential,
)


class CredentialVerifier(Protocol):
    """Client credentials now, OIDC ID tokens later (ADR-0014).

    Raises `InvalidCredentialsError` for an unknown, wrong or disabled credential.
    """

    async def verify(self, credential: Credential) -> VerifiedCredential: ...


class UserStore(Protocol):
    """Users and teams in Postgres. Raises `DependencyUnavailableError` when unreachable."""

    async def find_credentials(self, client_id: str) -> StoredCredentials | None: ...

    async def record_login(self, user_id: uuid.UUID, at: datetime) -> None: ...

    async def create_team(self, name: str) -> uuid.UUID:
        """Raises `ConflictError` when the name is taken."""
        ...

    async def find_team(self, name: str) -> uuid.UUID | None: ...

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
        """Raises `ConflictError` when the email or client id is taken."""
        ...

    async def set_active(self, client_id: str, *, active: bool) -> uuid.UUID | None:
        """The user's id, or None for an unknown client id."""
        ...

    async def set_secret_hash(self, client_id: str, secret_hash: str) -> uuid.UUID | None: ...


class RefreshTokenStore(Protocol):
    """Refresh tokens by peppered hash, grouped in families (one per login)."""

    async def add(self, token: NewRefreshToken, *, secret_hash: str) -> bool:
        """Store the first token of a new family, only while its user is active and still
        has `secret_hash`; False otherwise. Takes the same per-user lock as `rotate`."""
        ...

    async def rotate(
        self,
        token_hashes: Sequence[str],
        new_token_hash: str,
        *,
        now: datetime,
        refresh_ttl_seconds: int,
    ) -> Rotation:
        """In one transaction: revoke the live token matching any of `token_hashes` and
        store `new_token_hash` in its family, expiring after `refresh_ttl_seconds` or at
        the family's limit, whichever comes first. A token of a disabled user, or past
        its or its family's expiry, is `NotFound`.

        A token that is already revoked revokes its whole family (`Reused`). Of two
        concurrent rotations of one token, exactly one succeeds.
        """
        ...

    async def revoke_user(self, user_id: uuid.UUID, *, now: datetime) -> None: ...


class RateLimiter(Protocol):
    """GCRA: `limit` requests per `period_seconds`, as a burst or spread out.

    `cost=0` only checks. Raises `DependencyUnavailableError` when the store is
    unreachable: callers fail closed.
    """

    async def acquire(
        self, key: str, *, limit: int, period_seconds: int, now: datetime, cost: int = 1
    ) -> RateDecision: ...


class RevocationPublisher(Protocol):
    """Tells every gateway replica that a user or a token family is revoked."""

    async def publish(self, revocation: Revocation) -> None: ...
