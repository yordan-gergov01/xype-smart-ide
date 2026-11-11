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
    SessionKeyPolicy,
    SessionKeyService,
    SigningKey,
)
from common_lib.clock import Clock
from common_lib.keystore import RedisKeyStore
from common_lib.security import Kek, KekRing
from common_lib.settings import KeyedSecret, parse_keyed_secret


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


def _kek(value: KeyedSecret) -> Kek:
    kek_id, material = parse_keyed_secret(value)
    return Kek(kek_id, material)


def kek_ring(settings: BackendSettings) -> KekRing:
    previous = settings.kek_previous
    return KekRing(_kek(settings.kek_current), _kek(previous) if previous else None)


def auth_dependencies(
    settings: BackendSettings, database: Database, redis: Redis, keystore: Redis
) -> AuthDependencies:
    """`redis` is the cache, `keystore` the non-evicting key store (ADR-0015)."""
    users = SqlUserStore(database.write_sessions)
    return AuthDependencies(
        verifier=ClientCredentialVerifier(users, peppers(settings)),
        users=users,
        tokens=SqlRefreshTokenStore(database.write_sessions),
        limiter=RedisRateLimiter(redis),
        revocations=RedisRevocationPublisher(redis),
        keys=RedisKeyStore(keystore, kek_ring(settings)),
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


def session_key_service(
    settings: BackendSettings, deps: AuthDependencies, clock: Clock
) -> SessionKeyService:
    policy = SessionKeyPolicy(
        mints_per_minute=settings.session_keys_per_minute,
        max_active_per_user=settings.session_keys_max_active,
    )
    return SessionKeyService(deps.keys, deps.limiter, policy=policy, clock=clock)


def client_admin(settings: BackendSettings, deps: AuthDependencies, clock: Clock) -> ClientAdmin:
    return ClientAdmin(
        deps.users,
        deps.tokens,
        deps.revocations,
        deps.keys,
        peppers=peppers(settings),
        clock=clock,
    )
