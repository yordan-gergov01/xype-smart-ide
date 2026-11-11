"""The in-memory auth fakes meet the same contracts as the Postgres and Redis adapters
(tests/integration/test_auth_stores.py)."""

from collections.abc import Awaitable, Callable

import pytest
from support.auth import (
    FakeRateLimiter,
    FakeRefreshTokenStore,
    FakeRevocationPublisher,
    FakeUserStore,
)
from support.contracts import auth as contract

from backend.services.auth import RateLimiter, RefreshTokenStore, UserStore
from common_lib.auth import Revocation


@pytest.mark.parametrize("check", contract.STORES, ids=lambda c: c.__name__)
async def test_store_contract(
    check: Callable[[UserStore, RefreshTokenStore], Awaitable[None]],
) -> None:
    users = FakeUserStore()
    await check(users, FakeRefreshTokenStore(users))


@pytest.mark.parametrize("check", contract.LIMITER, ids=lambda c: c.__name__)
async def test_limiter_contract(check: Callable[[RateLimiter], Awaitable[None]]) -> None:
    await check(FakeRateLimiter())


async def test_revocation_contract() -> None:
    publisher = FakeRevocationPublisher()

    async def observed() -> list[Revocation]:
        return publisher.published

    await contract.assert_publishes_revocations(publisher, observed)
