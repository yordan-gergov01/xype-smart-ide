"""Contracts of the auth stores: the fakes (unit) and the Postgres and Redis adapters
(integration) all run them."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from backend.services.auth import (
    RateLimiter,
    RefreshTokenStore,
    RevocationPublisher,
    RevocationSubscriber,
    UserStore,
)
from common_lib.auth import (
    ConflictError,
    NewRefreshToken,
    NotFound,
    Reused,
    Revocation,
    Rotated,
    Rotation,
)

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
SECRET_HASH = "p$" + "0" * 64
TTL = 30 * 86_400


def _unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


async def new_user(users: UserStore, *, team: str | None = None) -> tuple[uuid.UUID, str]:
    team_name = team or _unique("team-")
    team_id = await users.find_team(team_name) or await users.create_team(team_name)
    client_id = _unique("xc_")
    user_id = await users.create_user(
        team_id=team_id,
        email=f"{client_id}@example.com",
        display_name="Test",
        role="developer",
        client_id=client_id,
        secret_hash=SECRET_HASH,
    )
    return user_id, client_id


def _hash() -> str:
    return uuid.uuid4().hex * 2


async def new_family(
    tokens: RefreshTokenStore, user_id: uuid.UUID, *, family_days: int = 90
) -> tuple[uuid.UUID, str]:
    family_id, token_hash = uuid.uuid4(), _hash()
    assert await tokens.add(
        first_token(user_id, family_id, token_hash, family_days), secret_hash=SECRET_HASH
    )
    return family_id, token_hash


def first_token(
    user_id: uuid.UUID, family_id: uuid.UUID, token_hash: str, family_days: int = 90
) -> NewRefreshToken:
    return NewRefreshToken(
        user_id=user_id,
        family_id=family_id,
        token_hash=token_hash,
        expires_at=NOW + timedelta(seconds=TTL),
        family_expires_at=NOW + timedelta(days=family_days),
    )


async def _rotate(
    tokens: RefreshTokenStore, token_hash: str, new_hash: str, at: datetime = NOW
) -> Rotation:
    return await tokens.rotate([_hash(), token_hash], new_hash, now=at, refresh_ttl_seconds=TTL)


# --- UserStore --------------------------------------------------------------------


async def assert_finds_credentials(users: UserStore, _tokens: RefreshTokenStore) -> None:
    user_id, client_id = await new_user(users)
    stored = await users.find_credentials(client_id)
    assert stored is not None
    assert stored.principal.user_id == user_id
    assert stored.principal.role == "developer"
    assert stored.is_active
    assert await users.find_credentials(_unique("xc_")) is None


async def assert_refuses_duplicates(users: UserStore, _tokens: RefreshTokenStore) -> None:
    team = _unique("team-")
    await users.create_team(team)
    with pytest.raises(ConflictError):
        await users.create_team(team)
    _, client_id = await new_user(users, team=team)
    team_id = await users.find_team(team)
    assert team_id is not None
    with pytest.raises(ConflictError):
        await users.create_user(
            team_id=team_id,
            email=f"{client_id}@example.com",
            display_name="Again",
            role="developer",
            client_id=_unique("xc_"),
            secret_hash="p$" + "1" * 64,
        )


async def assert_disables_and_rekeys(users: UserStore, _tokens: RefreshTokenStore) -> None:
    user_id, client_id = await new_user(users)
    assert await users.set_active(client_id, active=False) == user_id
    assert await users.set_secret_hash(client_id, "p$" + "2" * 64) == user_id
    stored = await users.find_credentials(client_id)
    assert stored is not None
    assert not stored.is_active
    assert stored.secret_hash == "p$" + "2" * 64
    assert await users.set_active(_unique("xc_"), active=True) is None


# --- RefreshTokenStore ------------------------------------------------------------


async def assert_rotates_within_the_family(users: UserStore, tokens: RefreshTokenStore) -> None:
    user_id, _ = await new_user(users)
    family_id, first = await new_family(tokens, user_id)
    second = _hash()
    rotated = await _rotate(tokens, first, second)
    assert isinstance(rotated, Rotated)
    assert (rotated.principal.user_id, rotated.family_id) == (user_id, family_id)
    assert isinstance(await _rotate(tokens, second, _hash()), Rotated)


async def assert_reuse_revokes_the_family(users: UserStore, tokens: RefreshTokenStore) -> None:
    user_id, _ = await new_user(users)
    family_id, first = await new_family(tokens, user_id)
    _, other_family = await new_family(tokens, user_id)
    second = _hash()
    await _rotate(tokens, first, second)
    assert await _rotate(tokens, first, _hash()) == Reused(user_id, family_id)
    # The family's newest token is revoked too; another family of the user is not.
    assert isinstance(await _rotate(tokens, second, _hash()), Reused)
    assert isinstance(await _rotate(tokens, other_family, _hash()), Rotated)


async def assert_refuses_unknown_expired_and_disabled(
    users: UserStore, tokens: RefreshTokenStore
) -> None:
    user_id, client_id = await new_user(users)
    _, live = await new_family(tokens, user_id)
    _, short = await new_family(tokens, user_id, family_days=1)
    assert await _rotate(tokens, _hash(), _hash()) == NotFound()
    assert await _rotate(tokens, live, _hash(), NOW + timedelta(seconds=TTL + 1)) == NotFound()
    assert await _rotate(tokens, short, _hash(), NOW + timedelta(days=2)) == NotFound()
    await users.set_active(client_id, active=False)
    assert await _rotate(tokens, live, _hash()) == NotFound()


async def assert_caps_new_tokens_at_the_family_limit(
    users: UserStore, tokens: RefreshTokenStore
) -> None:
    user_id, _ = await new_user(users)
    _, first = await new_family(tokens, user_id, family_days=2)
    second = _hash()
    await _rotate(tokens, first, second, NOW + timedelta(days=1))
    # Valid for 30 days by its own TTL, but the family ends after 2.
    assert await _rotate(tokens, second, _hash(), NOW + timedelta(days=3)) == NotFound()


async def assert_revokes_every_token_of_a_user(users: UserStore, tokens: RefreshTokenStore) -> None:
    user_id, _ = await new_user(users)
    _, a = await new_family(tokens, user_id)
    _, b = await new_family(tokens, user_id)
    await tokens.revoke_user(user_id, now=NOW)
    for token_hash in (a, b):
        assert isinstance(await _rotate(tokens, token_hash, _hash()), Reused)


async def assert_a_login_needs_the_verified_state(
    users: UserStore, tokens: RefreshTokenStore
) -> None:
    """A family starts only while the secret and the active flag are as verified."""
    user_id, client_id = await new_user(users)
    stale = first_token(user_id, uuid.uuid4(), _hash())
    await users.set_secret_hash(client_id, "p$" + "3" * 64)
    assert not await tokens.add(stale, secret_hash=SECRET_HASH)
    assert await tokens.add(stale, secret_hash="p$" + "3" * 64)
    await users.set_active(client_id, active=False)
    assert not await tokens.add(
        first_token(user_id, uuid.uuid4(), _hash()), secret_hash="p$" + "3" * 64
    )


STORES: tuple[Callable[[UserStore, RefreshTokenStore], Awaitable[None]], ...] = (
    assert_finds_credentials,
    assert_refuses_duplicates,
    assert_disables_and_rekeys,
    assert_rotates_within_the_family,
    assert_reuse_revokes_the_family,
    assert_refuses_unknown_expired_and_disabled,
    assert_caps_new_tokens_at_the_family_limit,
    assert_revokes_every_token_of_a_user,
    assert_a_login_needs_the_verified_state,
)


# --- RateLimiter ------------------------------------------------------------------


async def assert_allows_the_limit_then_refuses(limiter: RateLimiter) -> None:
    key = _unique("user:")
    for _ in range(200):
        assert (await limiter.acquire(key, limit=200, period_seconds=60, now=NOW)).allowed
    refused = await limiter.acquire(key, limit=200, period_seconds=60, now=NOW)
    assert not refused.allowed
    assert 0 < refused.retry_after_seconds <= 0.3


async def assert_refills_over_time(limiter: RateLimiter) -> None:
    key = _unique("user:")
    for _ in range(2):
        await limiter.acquire(key, limit=2, period_seconds=60, now=NOW)
    assert not (await limiter.acquire(key, limit=2, period_seconds=60, now=NOW)).allowed
    later = NOW + timedelta(seconds=30)
    assert (await limiter.acquire(key, limit=2, period_seconds=60, now=later)).allowed


async def assert_checking_costs_nothing(limiter: RateLimiter) -> None:
    key = _unique("login:")
    for _ in range(5):
        check = await limiter.acquire(key, limit=1, period_seconds=60, now=NOW, cost=0)
        assert check.allowed
    assert (await limiter.acquire(key, limit=1, period_seconds=60, now=NOW)).allowed
    assert not (await limiter.acquire(key, limit=1, period_seconds=60, now=NOW, cost=0)).allowed


async def assert_keys_are_independent(limiter: RateLimiter) -> None:
    a, b = _unique("user:"), _unique("user:")
    await limiter.acquire(a, limit=1, period_seconds=60, now=NOW)
    assert not (await limiter.acquire(a, limit=1, period_seconds=60, now=NOW)).allowed
    assert (await limiter.acquire(b, limit=1, period_seconds=60, now=NOW)).allowed


LIMITER: tuple[Callable[[RateLimiter], Awaitable[None]], ...] = (
    assert_allows_the_limit_then_refuses,
    assert_refills_over_time,
    assert_checking_costs_nothing,
    assert_keys_are_independent,
)


# --- RevocationPublisher / RevocationSubscriber -------------------------------------


async def assert_hears_revocations(
    publisher: RevocationPublisher,
    subscriber: RevocationSubscriber,
    *,
    settle: Callable[[], Awaitable[None]],
) -> None:
    """What one replica publishes, the subscriber hears, in order. `settle` waits until
    the subscription is in place (pub/sub drops what is sent before)."""
    sent = [Revocation("user", uuid.uuid4()), Revocation("family", uuid.uuid4())]
    received: list[Revocation] = []

    async def consume() -> None:
        async for revocation in subscriber.revocations():
            received.append(revocation)
            if len(received) == len(sent):
                return

    task = asyncio.create_task(consume())
    try:
        await settle()
        for revocation in sent:
            await publisher.publish(revocation)
        await asyncio.wait_for(task, timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert received == sent
