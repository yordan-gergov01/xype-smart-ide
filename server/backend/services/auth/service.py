"""Login, refresh and request authentication (ADR-0014).

Every outcome is logged with ids only: never a client id, secret or token.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from backend.services.auth.access_tokens import AccessTokenIssuer
from backend.services.auth.peppers import PepperRing, new_refresh_token
from backend.services.auth.ports import (
    CredentialVerifier,
    RateLimiter,
    RefreshTokenStore,
    RevocationPublisher,
    UserStore,
)
from common_lib.auth import (
    AccessClaims,
    Credential,
    InvalidCredentialsError,
    InvalidTokenError,
    NewRefreshToken,
    Principal,
    RateLimitedError,
    Reused,
    Revocation,
    Rotated,
    TokenPair,
)
from common_lib.clock import Clock

logger = structlog.get_logger(__name__)

MINUTE = 60


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    refresh_ttl_seconds: int
    family_max_age_seconds: int
    requests_per_minute: int
    login_failures_per_minute: int


@dataclass(frozen=True, slots=True)
class AuthDependencies:
    """The adapters the service uses; wired in the composition root."""

    verifier: CredentialVerifier
    users: UserStore
    tokens: RefreshTokenStore
    limiter: RateLimiter
    revocations: RevocationPublisher


class AuthService:
    def __init__(
        self,
        deps: AuthDependencies,
        *,
        issuer: AccessTokenIssuer,
        peppers: PepperRing,
        policy: AuthPolicy,
        clock: Clock,
    ) -> None:
        self._deps = deps
        self._issuer = issuer
        self._peppers = peppers
        self._policy = policy
        self._clock = clock

    async def login(self, credential: Credential) -> TokenPair:
        """A new token family (one per IDE window).

        Failures count towards a backoff per client id (keyed by its HMAC, so Redis holds
        no client id). Someone who knows a client id can delay that client's new logins
        while they keep failing; logged-in windows keep refreshing (ADR-0014).
        """
        failures = f"login:{self._peppers.token_hash(credential.client_id.encode())[:32]}"
        await self._limit(failures, self._policy.login_failures_per_minute, cost=0)
        try:
            verified = await self._deps.verifier.verify(credential)
        except InvalidCredentialsError:
            await self._limit(
                failures, self._policy.login_failures_per_minute, cost=1, raise_=False
            )
            logger.info("auth.login.failed")
            raise
        principal = verified.principal
        now = self._clock.now()
        family_id = uuid.uuid4()
        family_expires_at = now + timedelta(seconds=self._policy.family_max_age_seconds)
        refresh_token = new_refresh_token()
        first = NewRefreshToken(
            user_id=principal.user_id,
            family_id=family_id,
            token_hash=self._peppers.token_hash(refresh_token.encode()),
            expires_at=min(self._refresh_expiry(now), family_expires_at),
            family_expires_at=family_expires_at,
        )
        # Refused if the secret was rotated or the user disabled since `verify`.
        if not await self._deps.tokens.add(first, secret_hash=verified.secret_hash):
            logger.info("auth.login.failed")
            raise InvalidCredentialsError
        # A separate transaction: if it fails the login answers 503 and the unused token
        # row simply expires.
        await self._deps.users.record_login(principal.user_id, now)
        logger.info(
            "auth.login.succeeded", user_id=str(principal.user_id), family_id=str(family_id)
        )
        return self._pair(principal, family_id, refresh_token)

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate: the presented token is revoked and a new one issued in its family.
        Presenting a revoked token revokes the family and tells every replica."""
        now = self._clock.now()
        replacement = new_refresh_token()
        outcome = await self._deps.tokens.rotate(
            self._peppers.token_hashes(refresh_token.encode()),
            self._peppers.token_hash(replacement.encode()),
            now=now,
            refresh_ttl_seconds=self._policy.refresh_ttl_seconds,
        )
        if isinstance(outcome, Reused):
            await self._deps.revocations.publish(Revocation("family", outcome.family_id))
            logger.warning(
                "auth.refresh.reused",
                user_id=str(outcome.user_id),
                family_id=str(outcome.family_id),
            )
            raise InvalidTokenError
        if not isinstance(outcome, Rotated):
            raise InvalidTokenError
        return self._pair(outcome.principal, outcome.family_id, replacement)

    async def authenticate(self, access_token: str) -> AccessClaims:
        """A verified access token, counted against its user's rate limit."""
        claims = self._issuer.verify(access_token)
        await self._limit(f"user:{claims.principal.user_id}", self._policy.requests_per_minute)
        return claims

    async def _limit(self, key: str, limit: int, *, cost: int = 1, raise_: bool = True) -> None:
        decision = await self._deps.limiter.acquire(
            key, limit=limit, period_seconds=MINUTE, now=self._clock.now(), cost=cost
        )
        if not decision.allowed and raise_:
            raise RateLimitedError(decision.retry_after_seconds)

    def _refresh_expiry(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._policy.refresh_ttl_seconds)

    def _pair(self, principal: Principal, family_id: uuid.UUID, refresh_token: str) -> TokenPair:
        return TokenPair(
            access_token=self._issuer.issue(principal, family_id),
            refresh_token=refresh_token,
            expires_in_seconds=self._issuer.ttl_seconds,
        )
