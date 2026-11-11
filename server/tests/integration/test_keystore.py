"""`RedisKeyStore` meets the `KeyStore` contract against the `keystore` Redis as
deploy/docker-compose.yml runs it (TLS, the backend's ACL user, no eviction), stores only
wrapped key material, and refuses tampered or swapped records (rule 07, ADR-0015)."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import NoPermissionError, ResponseError
from support import containers
from support.backend import make_settings
from support.contracts import keystore as contract

from backend.core.connections import create_keystore_client
from backend.core.settings import BackendSettings
from common_lib.keystore import RedisKeyStore
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    REVOCATION_MEMORY_SECONDS,
    DecryptionError,
    Kek,
    KekRing,
    KeyStore,
)

pytestmark = pytest.mark.integration

DEFAULT_PASSWORD = "test-only-default"
BACKEND_PASSWORD = "test-only-not-a-secret"
KEK = KekRing(Kek("kek-1", SecretBytes(bytes(range(32)))))


@pytest.fixture(scope="module")
def keystore() -> Iterator[BackendSettings]:
    """Settings that reach the key store as the backend's ACL user."""
    env = {
        "REDIS_PASSWORD": DEFAULT_PASSWORD,
        "REDIS_BACKEND_USER": "backend",
        "REDIS_BACKEND_PASSWORD": BACKEND_PASSWORD,
    }
    with containers.redis(env, service="keystore") as container:
        yield make_settings(
            keystore_host=container.get_container_host_ip(),
            keystore_port=int(container.get_exposed_port(6379)),
            keystore_user="backend",
            keystore_password=BACKEND_PASSWORD,
        )


@pytest.fixture
async def client(keystore: BackendSettings) -> AsyncIterator[Redis]:
    """The operator's `default` user, to look at and tamper with what is stored."""
    admin = keystore.model_copy(
        update={"keystore_user": "default", "keystore_password": SecretStr(DEFAULT_PASSWORD)}
    )
    redis = create_keystore_client(admin)
    yield redis
    await redis.aclose()


@pytest.fixture
async def backend(keystore: BackendSettings) -> AsyncIterator[Redis]:
    redis = create_keystore_client(keystore)
    yield redis
    await redis.aclose()


@pytest.fixture
def store(backend: Redis) -> KeyStore:
    # Typed as the Protocol, so mypy checks the adapter against it.
    adapter: KeyStore = RedisKeyStore(backend, KEK)
    return adapter


@pytest.mark.parametrize("check", contract.ALL, ids=lambda c: c.__name__)
async def test_contract(store: KeyStore, check: Callable[[KeyStore], Awaitable[None]]) -> None:
    await check(store)


async def test_expires(store: KeyStore) -> None:
    await contract.assert_expires(store, ttl=1, wait=lambda: asyncio.sleep(1.2))


async def test_expired_keys_do_not_count(store: KeyStore) -> None:
    await contract.assert_expired_keys_do_not_count(store, wait=lambda: asyncio.sleep(1.2))


async def _record_json(client: Redis, key_id: str) -> dict[str, str]:
    raw = await client.get(f"xype:sk:{key_id}")
    assert raw is not None
    data: dict[str, str] = json.loads(raw)
    return data


async def test_stores_only_wrapped_keys(store: KeyStore, client: Redis) -> None:
    rec = contract.record()
    value = await contract.stored(store, rec)
    await store.bind_stream(rec.key_id, "session-a")
    forms = [value, value.hex().encode(), base64.b64encode(value)]
    async for name in client.scan_iter("xype:sk*"):
        content = await client.dump(name)
        assert content is not None
        assert not any(form in content for form in forms)
    assert set(await _record_json(client, rec.key_id)) == {
        "key_id",
        "user_id",
        "family_id",
        "created_at",
        "kek_id",
        "nonce",
        "wrapped",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "attacker"},
        {"family_id": "other"},
        {"created_at": "2030-01-01T00:00:00+00:00"},
        {"kek_id": "kek-9"},
        {"wrapped": "!!"},
        {"nonce": None},
    ],
    ids=["user", "family", "created", "kek", "bad-base64", "missing-field"],
)
async def test_refuses_a_tampered_record(
    store: KeyStore, client: Redis, change: dict[str, str | None]
) -> None:
    rec = contract.record(user_id="victim")
    await contract.stored(store, rec)
    data = await _record_json(client, rec.key_id)
    for field, value in change.items():
        if value is None:
            del data[field]
        else:
            data[field] = value
    await client.set(f"xype:sk:{rec.key_id}", json.dumps(data))
    with pytest.raises(DecryptionError):
        await store.get(rec.key_id)


async def test_refuses_a_record_copied_into_another_slot(store: KeyStore, client: Redis) -> None:
    a, b = contract.record(), contract.record()
    await contract.stored(store, a)
    await contract.stored(store, b)
    await client.set(f"xype:sk:{b.key_id}", json.dumps(await _record_json(client, a.key_id)))
    with pytest.raises(DecryptionError):
        await store.get(b.key_id)


async def test_revoke_deletes_bindings_and_counters(store: KeyStore, client: Redis) -> None:
    user, family = contract.unique("u-revoke"), contract.unique("f-revoke")
    rec = contract.record(user_id=user, family_id=family)
    await contract.stored(store, rec)
    await store.bind_stream(rec.key_id, "session-a")
    await store.advance_nonce(rec.key_id, contract.C2S, 3)
    await store.next_counter(rec.key_id, contract.S2C)
    await store.revoke_user(user, refuse_issued_until=datetime.now(UTC))
    left = [name async for name in client.scan_iter(f"xype:sk*{rec.key_id}*")]
    assert left == []
    assert not await client.exists(f"xype:sku:{user}", f"xype:skf:{family}")
    assert 0 < await client.ttl(f"xype:skr:u:{user}") <= REVOCATION_MEMORY_SECONDS


async def test_a_malformed_record_does_not_stop_revocation(store: KeyStore, client: Redis) -> None:
    user = contract.unique("u-mixed")
    bad, good = contract.record(user_id=user), contract.record(user_id=user)
    await contract.stored(store, bad)
    await contract.stored(store, good)
    await client.set(f"xype:sk:{bad.key_id}", "not json")
    await store.revoke_user(user, refuse_issued_until=datetime.now(UTC))
    assert not await client.exists(f"xype:sk:{bad.key_id}", f"xype:sk:{good.key_id}")


async def test_unwraps_under_the_previous_kek(backend: Redis) -> None:
    old = RedisKeyStore(backend, KEK)
    rec = contract.record()
    value = await contract.stored(old, rec)
    rotated = RedisKeyStore(backend, KekRing(Kek("kek-2", SecretBytes(bytes(32))), KEK.current))
    _, key = await rotated.get(rec.key_id)
    assert key.reveal() == value


async def test_backend_acl_reaches_only_session_keys(backend: Redis) -> None:
    for refused in (
        backend.set("xype:rl:x", "1"),
        backend.get("xype:auth:x"),
        backend.publish("xype:auth:revoked", "x"),
        backend.keys("*"),
        backend.config_get("maxmemory"),
        backend.eval("return 1", 0),
    ):
        with pytest.raises(NoPermissionError):
            await refused


async def test_the_key_store_never_evicts(client: Redis) -> None:
    assert (await client.config_get("maxmemory-policy"))["maxmemory-policy"] == "noeviction"


async def test_revokes_on_a_full_store(store: KeyStore, client: Redis) -> None:
    """noeviction refuses writes when full; a revocation must still go through."""
    user = contract.unique("u-full")
    rec = contract.record(user_id=user)
    await contract.stored(store, rec)
    filler = "x" * 65536

    async def fill() -> None:
        for i in range(1000):
            await client.set(f"xype:sk:filler:{i}", filler)

    with pytest.raises(ResponseError, match="maxmemory"):
        await fill()
    try:
        # Nothing to delete first: the script's only write is the tombstone.
        family = contract.unique("f-full")
        await store.revoke_family(family)
        assert await client.exists(f"xype:skr:f:{family}")
        await store.revoke_user(user, refuse_issued_until=datetime.now(UTC))
        assert not await client.exists(f"xype:sk:{rec.key_id}")
        assert await client.exists(f"xype:skr:u:{user}")
    finally:
        async for name in client.scan_iter("xype:sk:filler:*"):
            await client.delete(name)
