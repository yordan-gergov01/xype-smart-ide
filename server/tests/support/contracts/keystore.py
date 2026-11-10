"""The `KeyStore` contract: `FakeKeyStore` and `RedisKeyStore` both run it."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    Direction,
    DuplicateKeyError,
    KeyBoundElsewhereError,
    KeyNotFoundError,
    KeyStore,
    NonceError,
    SessionKeyRecord,
    new_key,
)

TTL = 3600
C2S, S2C = Direction.CLIENT_TO_SERVER, Direction.SERVER_TO_CLIENT


def record(*, user_id: str = "user-1", family_id: str = "family-1") -> SessionKeyRecord:
    created = datetime(2026, 11, 10, tzinfo=UTC)
    return SessionKeyRecord(str(uuid.uuid4()), user_id, family_id, created)


async def stored(store: KeyStore, rec: SessionKeyRecord, *, ttl: int = TTL) -> bytes:
    key = new_key()
    value = key.reveal()
    await store.put(rec, key, ttl_seconds=ttl)
    key.wipe()
    return value


async def assert_round_trips(store: KeyStore) -> None:
    rec = record()
    value = await stored(store, rec)
    got_record, got_key = await store.get(rec.key_id)
    assert got_record == rec
    assert isinstance(got_key, SecretBytes)
    assert got_key.reveal() == value
    with pytest.raises(KeyNotFoundError):
        await store.get(str(uuid.uuid4()))


async def assert_never_overwrites(store: KeyStore) -> None:
    rec = record()
    await stored(store, rec)
    await store.advance_nonce(rec.key_id, C2S, 5)
    with pytest.raises(DuplicateKeyError):
        await stored(store, rec)
    # The counters did not restart.
    with pytest.raises(NonceError):
        await store.advance_nonce(rec.key_id, C2S, 5)
    with pytest.raises(InvalidValueError):
        await stored(store, record(), ttl=0)


async def assert_binds_one_stream(store: KeyStore) -> None:
    rec = record()
    await stored(store, rec)
    await store.bind_stream(rec.key_id, "session-a")
    await store.bind_stream(rec.key_id, "session-a")
    with pytest.raises(KeyBoundElsewhereError):
        await store.bind_stream(rec.key_id, "session-b")
    with pytest.raises(KeyNotFoundError):
        await store.bind_stream(str(uuid.uuid4()), "session-a")


async def assert_counters(store: KeyStore) -> None:
    rec = record()
    await stored(store, rec)
    await store.advance_nonce(rec.key_id, C2S, 1)
    await store.advance_nonce(rec.key_id, C2S, 5)
    for replayed in (5, 4):
        with pytest.raises(NonceError):
            await store.advance_nonce(rec.key_id, C2S, replayed)
    assert [await store.next_counter(rec.key_id, S2C) for _ in range(3)] == [1, 2, 3]
    with pytest.raises(KeyNotFoundError):
        await store.advance_nonce(str(uuid.uuid4()), C2S, 1)
    with pytest.raises(KeyNotFoundError):
        await store.next_counter(str(uuid.uuid4()), S2C)


async def assert_server_side_directions_only(store: KeyStore) -> None:
    """The server never sends client-to-server or receives server-to-client."""
    rec = record()
    await stored(store, rec)
    with pytest.raises(NonceError):
        await store.next_counter(rec.key_id, C2S)
    with pytest.raises(NonceError):
        await store.advance_nonce(rec.key_id, S2C, 1)


async def assert_revokes(store: KeyStore) -> None:
    mine, other_family, other_user = (
        record(user_id="u-a", family_id="f-a"),
        record(user_id="u-a", family_id="f-b"),
        record(user_id="u-b", family_id="f-c"),
    )
    for rec in (mine, other_family, other_user):
        await stored(store, rec)
    await store.bind_stream(mine.key_id, "session-a")
    await store.revoke_family("f-a")
    for operation in (
        store.get(mine.key_id),
        store.bind_stream(mine.key_id, "session-a"),
        store.advance_nonce(mine.key_id, C2S, 1),
        store.next_counter(mine.key_id, S2C),
    ):
        with pytest.raises(KeyNotFoundError):
            await operation
    await store.get(other_family.key_id)
    await store.revoke_user("u-a")
    with pytest.raises(KeyNotFoundError):
        await store.get(other_family.key_id)
    await store.get(other_user.key_id)


async def assert_expires(store: KeyStore, *, ttl: int, wait: Callable[[], Awaitable[None]]) -> None:
    """`wait` lets more than `ttl` seconds pass."""
    rec = record()
    await stored(store, rec, ttl=ttl)
    await store.bind_stream(rec.key_id, "session-a")
    await wait()
    for operation in (
        store.get(rec.key_id),
        store.bind_stream(rec.key_id, "session-a"),
        store.advance_nonce(rec.key_id, C2S, 1),
        store.next_counter(rec.key_id, S2C),
    ):
        with pytest.raises(KeyNotFoundError):
            await operation


ALL = (
    assert_round_trips,
    assert_never_overwrites,
    assert_binds_one_stream,
    assert_counters,
    assert_server_side_directions_only,
    assert_revokes,
)
