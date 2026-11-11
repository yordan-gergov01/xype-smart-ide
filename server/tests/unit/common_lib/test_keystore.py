from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from support.clock import FakeClock
from support.contracts import keystore as contract
from support.keystore import FakeKeyStore

from common_lib.security import KeyStore


def make() -> tuple[FakeKeyStore, FakeClock]:
    clock = FakeClock(datetime(2026, 11, 10, tzinfo=UTC))
    return FakeKeyStore(clock), clock


# mypy checks the fake against the Protocol.
_fake: KeyStore = make()[0]


@pytest.mark.parametrize("check", contract.ALL, ids=lambda c: c.__name__)
async def test_contract(check: Callable[[KeyStore], Awaitable[None]]) -> None:
    await check(make()[0])


async def test_expires() -> None:
    store, clock = make()

    async def wait() -> None:
        clock.advance(timedelta(seconds=2))

    await contract.assert_expires(store, ttl=1, wait=wait)
