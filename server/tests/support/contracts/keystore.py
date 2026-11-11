"""The `KeyStore` contract: `FakeKeyStore` and `RedisKeyStore` both run it."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    Direction,
    DuplicateKeyError,
    KeyBoundElsewhereError,
    KeyNotFoundError,
    KeyRevokedError,
    KeyStore,
    NonceError,
    SessionKeyRecord,
    new_key,
)

TTL = 3600
CAP = 8
C2S, S2C = Direction.CLIENT_TO_SERVER, Direction.SERVER_TO_CLIENT
CREATED = datetime(2026, 11, 10, tzinfo=UTC)
# Credentials issued long ago: revocations "now" (wall clock) come after them.
ISSUED = datetime(2020, 1, 1, tzinfo=UTC)


def record(
    *, user_id: str = "user-1", family_id: str = "family-1", created: datetime = CREATED
) -> SessionKeyRecord:
    return SessionKeyRecord(str(uuid.uuid4()), user_id, family_id, created)


def unique(name: str) -> str:
    """Ids per test, so tombstones of one check never refuse keys of another."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


async def stored(
    store: KeyStore,
    rec: SessionKeyRecord,
    *,
    ttl: int = TTL,
    cap: int = CAP,
    issued: datetime = ISSUED,
) -> bytes:
    key = new_key()
    value = key.reveal()
    await store.put(rec, key, ttl_seconds=ttl, max_active=cap, credentials_issued_at=issued)
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
    with pytest.raises(InvalidValueError):
        await stored(store, record(), cap=0)


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


async def _assert_gone(store: KeyStore, key_id: str) -> None:
    for operation in (
        store.get(key_id),
        store.bind_stream(key_id, "session-a"),
        store.advance_nonce(key_id, C2S, 1),
        store.next_counter(key_id, S2C),
    ):
        with pytest.raises(KeyNotFoundError):
            await operation


async def assert_revokes(store: KeyStore) -> None:
    user_a, user_b, family_a = unique("u-a"), unique("u-b"), unique("f-a")
    mine, other_family, other_user = (
        record(user_id=user_a, family_id=family_a),
        record(user_id=user_a, family_id=unique("f-b")),
        record(user_id=user_b, family_id=unique("f-c")),
    )
    for rec in (mine, other_family, other_user):
        await stored(store, rec)
    await store.bind_stream(mine.key_id, "session-a")
    await store.revoke_family(family_a)
    await _assert_gone(store, mine.key_id)
    await store.get(other_family.key_id)
    await store.revoke_user(user_a, refuse_issued_until=datetime.now(UTC))
    with pytest.raises(KeyNotFoundError):
        await store.get(other_family.key_id)
    await store.get(other_user.key_id)


async def assert_refuses_keys_after_a_revocation(store: KeyStore) -> None:
    """A token that outlives a revocation (up to 15 min) mints no new key."""
    user, family = unique("u"), unique("f")
    await store.revoke_family(family)
    with pytest.raises(KeyRevokedError):
        await stored(store, record(user_id=user, family_id=family))
    await stored(store, record(user_id=user, family_id=unique("f")))
    revoked_at = datetime.now(UTC)
    await store.revoke_user(user, refuse_issued_until=revoked_at)
    for issued in (ISSUED, revoked_at):
        with pytest.raises(KeyRevokedError):
            await stored(store, record(user_id=user, family_id=unique("f")), issued=issued)
    # Credentials issued after the revocation (a new login after a rotation) work.
    later = revoked_at + timedelta(seconds=1)
    await stored(store, record(user_id=user, family_id=unique("f")), issued=later)


async def assert_caps_active_keys_per_user(store: KeyStore) -> None:
    """Over the cap, the oldest of the user's other keys go, with bindings and counters."""
    user = unique("u")
    first, second, third = (
        record(user_id=user, created=CREATED + timedelta(minutes=n)) for n in range(3)
    )
    for rec in (first, second):
        await stored(store, rec, cap=2)
    await store.bind_stream(first.key_id, "session-a")
    await stored(store, record(user_id=unique("u")), cap=1)
    await stored(store, third, cap=2)
    await _assert_gone(store, first.key_id)
    await store.get(second.key_id)
    await store.get(third.key_id)
    # A new key with an older clock reading (another replica) stays; the oldest other goes.
    older = record(user_id=user, created=CREATED - timedelta(hours=1))
    await stored(store, older, cap=2)
    await store.get(older.key_id)
    await store.get(third.key_id)
    with pytest.raises(KeyNotFoundError):
        await store.get(second.key_id)


async def assert_expires(store: KeyStore, *, ttl: int, wait: Callable[[], Awaitable[None]]) -> None:
    """`wait` lets more than `ttl` seconds pass."""
    rec = record()
    await stored(store, rec, ttl=ttl)
    await store.bind_stream(rec.key_id, "session-a")
    await wait()
    await _assert_gone(store, rec.key_id)


async def assert_expired_keys_do_not_count(
    store: KeyStore, *, wait: Callable[[], Awaitable[None]]
) -> None:
    """`wait` lets more than 1 second pass."""
    user = unique("u")
    await stored(store, record(user_id=user, created=CREATED), ttl=1)
    await wait()
    kept = record(user_id=user, created=CREATED + timedelta(minutes=1))
    newest = record(user_id=user, created=CREATED + timedelta(minutes=2))
    for rec in (kept, newest):
        await stored(store, rec, cap=2)
    await store.get(kept.key_id)
    await store.get(newest.key_id)


ALL = (
    assert_round_trips,
    assert_never_overwrites,
    assert_binds_one_stream,
    assert_counters,
    assert_server_side_directions_only,
    assert_revokes,
    assert_refuses_keys_after_a_revocation,
    assert_caps_active_keys_per_user,
)
