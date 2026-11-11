"""Builds the auth feature from the settings; shared by the two composition roots
(`backend.main`, `backend.admin_cli`), so both use the same keys and adapters."""

from redis.asyncio import Redis

from backend.core.connections import Database
from backend.core.settings import BackendSettings
from backend.dal.auth import (
    RedisRateLimiter,
    RedisRevocationPublisher,
    SqlRefreshTokenStore,
    SqlUserStore,
)
from backend.services.auth import (
    AccessTokenIssuer,
    AuthDependencies,
    AuthPolicy,
    AuthService,
    ClientAdmin,
    ClientCredentialVerifier,
    Pepper,
    PepperRing,
    SigningKey,
)
from common_lib.clock import Clock
from common_lib.settings import parse_keyed_secret


def peppers(settings: BackendSettings) -> PepperRing:
    current = Pepper(*parse_keyed_secret(settings.auth_pepper_current))
    previous = settings.auth_pepper_previous
    return PepperRing(current, Pepper(*parse_keyed_secret(previous)) if previous else None)


def access_token_issuer(settings: BackendSettings, clock: Clock) -> AccessTokenIssuer:
    current = SigningKey.from_seed(*parse_keyed_secret(settings.auth_signing_key_current))
    previous = settings.auth_signing_key_previous
    return AccessTokenIssuer(
        current,
        SigningKey.from_seed(*parse_keyed_secret(previous)) if previous else None,
        issuer=settings.auth_issuer,
        ttl_seconds=settings.auth_access_ttl_s,
        clock=clock,
    )


def auth_dependencies(
    settings: BackendSettings, database: Database, redis: Redis
) -> AuthDependencies:
    users = SqlUserStore(database.write_sessions)
    return AuthDependencies(
        verifier=ClientCredentialVerifier(users, peppers(settings)),
        users=users,
        tokens=SqlRefreshTokenStore(database.write_sessions),
        limiter=RedisRateLimiter(redis),
        revocations=RedisRevocationPublisher(redis),
    )


def auth_service(settings: BackendSettings, deps: AuthDependencies, clock: Clock) -> AuthService:
    return AuthService(
        deps,
        issuer=access_token_issuer(settings, clock),
        peppers=peppers(settings),
        policy=AuthPolicy(
            refresh_ttl_seconds=settings.auth_refresh_ttl_s,
            family_max_age_seconds=settings.auth_family_max_age_s,
            requests_per_minute=settings.rate_limit_per_minute,
            login_failures_per_minute=settings.auth_login_failures_per_minute,
        ),
        clock=clock,
    )


def client_admin(settings: BackendSettings, deps: AuthDependencies, clock: Clock) -> ClientAdmin:
    return ClientAdmin(
        deps.users, deps.tokens, deps.revocations, peppers=peppers(settings), clock=clock
    )
