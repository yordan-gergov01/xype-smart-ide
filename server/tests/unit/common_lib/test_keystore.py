from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from support.clock import FakeClock
from support.contracts import keystore as contract
from support.keystore import FakeKeyStore

from common_lib.security import (
    REVOCATION_MEMORY_SECONDS,
    KeyNotFoundError,
    KeyStore,
    check_key_age,
)


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


async def test_expired_keys_do_not_count() -> None:
    store, clock = make()

    async def wait() -> None:
        clock.advance(timedelta(seconds=2))

    await contract.assert_expired_keys_do_not_count(store, wait=wait)


async def test_revocations_are_remembered_for_a_while_only() -> None:
    store, clock = make()
    await store.revoke_family("f-old")
    clock.advance(timedelta(seconds=REVOCATION_MEMORY_SECONDS + 1))
    await contract.stored(store, contract.record(family_id="f-old"))


def test_a_key_is_usable_for_twelve_hours() -> None:
    rec = contract.record()
    check_key_age(rec, rec.created_at + timedelta(hours=12) - timedelta(seconds=1))
    with pytest.raises(KeyNotFoundError):
        check_key_age(rec, rec.created_at + timedelta(hours=12))
