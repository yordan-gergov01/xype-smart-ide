"""`RedisKeyStore` meets the `KeyStore` contract against a real Redis, stores only
wrapped key material, and refuses tampered or swapped records (rule 07)."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import yaml
from redis.asyncio import Redis
from support.contracts import keystore as contract
from testcontainers.community.redis import RedisContainer

from common_lib.keystore import RedisKeyStore
from common_lib.secret_bytes import SecretBytes
from common_lib.security import DecryptionError, Kek, KekRing, KeyStore

pytestmark = pytest.mark.integration

_COMPOSE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml").read_text("utf-8")
)
REDIS_IMAGE = _COMPOSE["services"]["redis"]["image"]
TEST_PASSWORD = "test-only-not-a-secret"
KEK = KekRing(Kek("kek-1", SecretBytes(bytes(range(32)))))


@pytest.fixture(scope="module")
def redis_url() -> Iterator[tuple[str, int]]:
    with RedisContainer(REDIS_IMAGE, password=TEST_PASSWORD) as container:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379))


@pytest.fixture
async def client(redis_url: tuple[str, int]) -> AsyncIterator[Redis]:
    host, port = redis_url
    redis = Redis(host=host, port=port, password=TEST_PASSWORD, socket_timeout=5)
    yield redis
    await redis.aclose()


@pytest.fixture
def store(client: Redis) -> KeyStore:
    # Typed as the Protocol, so mypy checks the adapter against it.
    adapter: KeyStore = RedisKeyStore(client, KEK)
    return adapter


@pytest.mark.parametrize("check", contract.ALL, ids=lambda c: c.__name__)
async def test_contract(store: KeyStore, check: Callable[[KeyStore], Awaitable[None]]) -> None:
    await check(store)


async def test_expires(store: KeyStore) -> None:
    await contract.assert_expires(store, ttl=1, wait=lambda: asyncio.sleep(1.2))


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
    rec = contract.record(user_id="u-revoke", family_id="f-revoke")
    await contract.stored(store, rec)
    await store.bind_stream(rec.key_id, "session-a")
    await store.advance_nonce(rec.key_id, contract.C2S, 3)
    await store.next_counter(rec.key_id, contract.S2C)
    await store.revoke_user("u-revoke")
    left = [name async for name in client.scan_iter(f"xype:sk*{rec.key_id}*")]
    assert left == []
    assert not await client.exists("xype:sku:u-revoke", "xype:skf:f-revoke")


async def test_a_malformed_record_does_not_stop_revocation(store: KeyStore, client: Redis) -> None:
    bad, good = contract.record(user_id="u-mixed"), contract.record(user_id="u-mixed")
    await contract.stored(store, bad)
    await contract.stored(store, good)
    await client.set(f"xype:sk:{bad.key_id}", "not json")
    await store.revoke_user("u-mixed")
    assert not await client.exists(f"xype:sk:{bad.key_id}", f"xype:sk:{good.key_id}")


async def test_unwraps_under_the_previous_kek(client: Redis) -> None:
    old = RedisKeyStore(client, KEK)
    rec = contract.record()
    value = await contract.stored(old, rec)
    rotated = RedisKeyStore(client, KekRing(Kek("kek-2", SecretBytes(bytes(32))), KEK.current))
    _, key = await rotated.get(rec.key_id)
    assert key.reveal() == value
