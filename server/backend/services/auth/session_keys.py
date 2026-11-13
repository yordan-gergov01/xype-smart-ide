"""Session keys: the X25519 exchange behind `POST /api/v1/auth/session-key` (ADR-0015).

The server mints a random session key, stores it wrapped under the KEK in the key store,
and hands it to the client wrapped under a key both sides derive from the exchange. The
same response carries the HMAC keys of the requested repositories that the caller's team
may access, re-wrapped for the client (ADR-0016). No key byte reaches a log line, and
every copy this process holds is wiped.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from backend.services.auth.ports import RateLimiter, RepositoryKeySource
from common_lib.auth import AccessClaims, RateLimitedError
from common_lib.clock import Clock
from common_lib.domain import uuid7
from common_lib.repositories import StoredRepositoryKey
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    SESSION_KEY_MAX_AGE_SECONDS,
    SESSION_KEY_TTL_SECONDS,
    DecryptionError,
    KekPurpose,
    KekRing,
    KeyStore,
    SessionKeyRecord,
    new_key,
    repository_key_aad,
    seal_repository_key,
    seal_session_key,
    server_exchange,
    unwrap_key,
)

logger = structlog.get_logger(__name__)

MINUTE = 60


@dataclass(frozen=True, slots=True)
class SessionKeyPolicy:
    mints_per_minute: int
    max_active_per_user: int


@dataclass(frozen=True, slots=True)
class IssuedRepositoryKey:
    """`wrapped_key` is `nonce ‖ AES-256-GCM` under the exchange's wrapping key, with
    AAD `"repo-hmac" ‖ repository_id ‖ hmac_key_id`."""

    repository_id: uuid.UUID
    hmac_key_id: str
    wrapped_key: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class MintedSessionKey:
    """What the client receives: `wrapped_key` is `nonce ‖ AES-256-GCM` under the
    exchange's wrapping key, and `expires_at` is when streams stop accepting the key."""

    key_id: str
    server_public_key: bytes
    wrapped_key: bytes = field(repr=False)
    expires_at: datetime
    repository_keys: tuple[IssuedRepositoryKey, ...] = ()


class SessionKeyService:
    def __init__(
        self,
        keys: KeyStore,
        limiter: RateLimiter,
        repository_keys: RepositoryKeySource,
        ring: KekRing,
        *,
        policy: SessionKeyPolicy,
        clock: Clock,
    ) -> None:
        self._keys = keys
        self._limiter = limiter
        self._repository_keys = repository_keys
        self._ring = ring
        self._policy = policy
        self._clock = clock

    async def mint(
        self,
        claims: AccessClaims,
        client_public_key: bytes,
        repository_ids: Collection[uuid.UUID] = (),
    ) -> MintedSessionKey:
        """Raises `KeyExchangeError` for a bad public key, `RateLimitedError` past the
        per-user limit and `KeyRevokedError` when the caller's family or user was revoked
        after the access token was issued. Repository ids the team may not access are
        left out silently, exactly like unknown ones."""
        user_id = str(claims.principal.user_id)
        await self._limit(user_id)
        # Read before anything is stored, so a failed read leaves no orphan session key.
        stored = await self._repository_keys.active_keys(
            claims.principal.team_id, frozenset(repository_ids)
        )
        now = self._clock.now()
        key_id = str(uuid7(now))
        session_key = new_key()
        try:
            exchange = server_exchange(client_public_key, key_id=key_id, user_id=user_id)
            try:
                wrapped = seal_session_key(exchange.wrapping_key, key_id, session_key)
                issued = tuple(self._reseal(exchange.wrapping_key, key) for key in stored)
            finally:
                exchange.wrapping_key.wipe()
            await self._keys.put(
                SessionKeyRecord(key_id, user_id, str(claims.family_id), now),
                session_key,
                ttl_seconds=SESSION_KEY_TTL_SECONDS,
                max_active=self._policy.max_active_per_user,
                credentials_issued_at=claims.issued_at,
            )
        finally:
            session_key.wipe()
        logger.info(
            "auth.session_key.minted", user_id=user_id, key_id=key_id, repositories=len(issued)
        )
        return MintedSessionKey(
            key_id=key_id,
            server_public_key=exchange.server_public_key,
            wrapped_key=wrapped,
            expires_at=now + timedelta(seconds=SESSION_KEY_MAX_AGE_SECONDS),
            repository_keys=issued,
        )

    def _reseal(
        self, wrapping_key: SecretBytes, stored: StoredRepositoryKey
    ) -> IssuedRepositoryKey:
        """Unwraps one repository key from the KEK and wraps it for the client. A row that
        fails authentication fails the whole exchange (`DecryptionError`)."""
        repository_id = str(stored.repository_id)
        aad = repository_key_aad(repository_id, stored.hmac_key_id, stored.wrapped_key.kek_id)
        try:
            key = unwrap_key(self._ring, KekPurpose.REPO_KEYS, stored.wrapped_key, aad)
        except DecryptionError:
            # Ids only: they point an operator at the row (a tampered row or a retired KEK).
            logger.error(
                "auth.repository_key.unwrap_failed",
                repository_id=repository_id,
                hmac_key_id=stored.hmac_key_id,
                kek_id=stored.wrapped_key.kek_id,
            )
            raise
        try:
            sealed = seal_repository_key(wrapping_key, repository_id, stored.hmac_key_id, key)
        finally:
            key.wipe()
        return IssuedRepositoryKey(stored.repository_id, stored.hmac_key_id, sealed)

    async def _limit(self, user_id: str) -> None:
        decision = await self._limiter.acquire(
            f"session-key:{user_id}",
            limit=self._policy.mints_per_minute,
            period_seconds=MINUTE,
            now=self._clock.now(),
        )
        if not decision.allowed:
            raise RateLimitedError(decision.retry_after_seconds)
