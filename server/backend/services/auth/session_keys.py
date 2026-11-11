"""Session keys: the X25519 exchange behind `POST /api/v1/auth/session-key` (ADR-0015).

The server mints a random session key, stores it wrapped under the KEK in the key store,
and hands it to the client wrapped under a key both sides derive from the exchange. The
key bytes never reach a log line, and every copy this process holds is wiped.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from backend.services.auth.ports import RateLimiter
from common_lib.auth import AccessClaims, RateLimitedError
from common_lib.clock import Clock
from common_lib.domain import uuid7
from common_lib.security import (
    SESSION_KEY_MAX_AGE_SECONDS,
    SESSION_KEY_TTL_SECONDS,
    KeyStore,
    SessionKeyRecord,
    new_key,
    seal_session_key,
    server_exchange,
)

logger = structlog.get_logger(__name__)

MINUTE = 60


@dataclass(frozen=True, slots=True)
class SessionKeyPolicy:
    mints_per_minute: int
    max_active_per_user: int


@dataclass(frozen=True, slots=True)
class MintedSessionKey:
    """What the client receives: `wrapped_key` is `nonce ‖ AES-256-GCM` under the
    exchange's wrapping key, and `expires_at` is when streams stop accepting the key."""

    key_id: str
    server_public_key: bytes
    wrapped_key: bytes = field(repr=False)
    expires_at: datetime


class SessionKeyService:
    def __init__(
        self, keys: KeyStore, limiter: RateLimiter, *, policy: SessionKeyPolicy, clock: Clock
    ) -> None:
        self._keys = keys
        self._limiter = limiter
        self._policy = policy
        self._clock = clock

    async def mint(self, claims: AccessClaims, client_public_key: bytes) -> MintedSessionKey:
        """Raises `KeyExchangeError` for a bad public key, `RateLimitedError` past the
        per-user limit and `KeyRevokedError` when the caller's family or user was revoked
        after the access token was issued."""
        user_id = str(claims.principal.user_id)
        await self._limit(user_id)
        now = self._clock.now()
        key_id = str(uuid7(now))
        session_key = new_key()
        try:
            exchange = server_exchange(client_public_key, key_id=key_id, user_id=user_id)
            try:
                wrapped = seal_session_key(exchange.wrapping_key, key_id, session_key)
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
        logger.info("auth.session_key.minted", user_id=user_id, key_id=key_id)
        return MintedSessionKey(
            key_id=key_id,
            server_public_key=exchange.server_public_key,
            wrapped_key=wrapped,
            expires_at=now + timedelta(seconds=SESSION_KEY_MAX_AGE_SECONDS),
        )

    async def _limit(self, user_id: str) -> None:
        decision = await self._limiter.acquire(
            f"session-key:{user_id}",
            limit=self._policy.mints_per_minute,
            period_seconds=MINUTE,
            now=self._clock.now(),
        )
        if not decision.allowed:
            raise RateLimitedError(decision.retry_after_seconds)
