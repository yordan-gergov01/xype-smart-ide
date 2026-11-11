"""The auth adapters meet the shared contracts against Postgres (migrated, app role) and
Redis (TLS, the backend's ACL user), as in deploy/docker-compose.yml (ADR-0014)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict

import pytest
from redis.asyncio import Redis
from redis.exceptions import NoPermissionError, ResponseError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from support import containers
from support.contracts import auth as contract
from support.database import Database, alembic, postgres
from support.tls import ca_file

from backend.dal.auth import (
    REVOCATION_CHANNEL,
    RedisRateLimiter,
    RedisRevocationPublisher,
    SqlRefreshTokenStore,
    SqlUserStore,
)
from backend.services.auth import RateLimiter, RefreshTokenStore, UserStore
from common_lib.auth import Reused, Revocation, Rotated
from common_lib.models import RefreshToken, User
from common_lib.models.connection import postgres_url
from common_lib.tls import MINIMUM_TLS, client_context

pytestmark = pytest.mark.integration

BACKEND_USER, BACKEND_PASSWORD = "backend", "test-only-backend"
DEFAULT_PASSWORD = "test-only-default"


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    with postgres() as db:
        alembic(db, ("upgrade", "head"))
        yield db


@pytest.fixture
async def sessions(database: Database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = database.app_settings()
    engine = create_async_engine(
        postgres_url(settings), connect_args={"ssl": client_context(ca_file())}
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(scope="module")
def redis_address() -> Iterator[tuple[str, int]]:
    env = {
        "REDIS_PASSWORD": DEFAULT_PASSWORD,
        "REDIS_BACKEND_USER": BACKEND_USER,
        "REDIS_BACKEND_PASSWORD": BACKEND_PASSWORD,
    }
    with containers.redis(env) as container:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379))


def _redis(address: tuple[str, int], user: str, password: str) -> Redis:
    return Redis(
        host=address[0],
        port=address[1],
        username=user,
        password=password,
        ssl=True,
        ssl_ca_certs=str(ca_file()),
        ssl_min_version=MINIMUM_TLS,
        socket_timeout=5,
    )


@pytest.fixture
async def backend_redis(redis_address: tuple[str, int]) -> AsyncIterator[Redis]:
    client = _redis(redis_address, BACKEND_USER, BACKEND_PASSWORD)
    yield client
    await client.aclose()


@pytest.mark.parametrize("check", contract.STORES, ids=lambda c: c.__name__)
async def test_store_contract(
    sessions: async_sessionmaker[AsyncSession],
    check: Callable[[UserStore, RefreshTokenStore], Awaitable[None]],
) -> None:
    await check(SqlUserStore(sessions), SqlRefreshTokenStore(sessions))


@pytest.mark.parametrize("check", contract.LIMITER, ids=lambda c: c.__name__)
async def test_limiter_contract(
    backend_redis: Redis, check: Callable[[RateLimiter], Awaitable[None]]
) -> None:
    await check(RedisRateLimiter(backend_redis))


async def test_two_concurrent_refreshes_give_one_success(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    users, tokens = SqlUserStore(sessions), SqlRefreshTokenStore(sessions)
    user_id, _ = await contract.new_user(users)
    _, token_hash = await contract.new_family(tokens, user_id)
    new_hashes = [uuid.uuid4().hex * 2 for _ in range(2)]
    outcomes = await asyncio.gather(
        *(
            tokens.rotate([token_hash], new, now=contract.NOW, refresh_ttl_seconds=60)
            for new in new_hashes
        )
    )
    assert sorted(type(o).__name__ for o in outcomes) == [Reused.__name__, Rotated.__name__]
    # The loser revoked the whole family, including the winner's new token.
    winner = new_hashes[[isinstance(o, Rotated) for o in outcomes].index(True)]
    after = await tokens.rotate(
        [winner], uuid.uuid4().hex * 2, now=contract.NOW, refresh_ttl_seconds=60
    )
    assert isinstance(after, Reused)


# Deterministic races: a transaction holds the user's row lock, as a rotation in flight
# does, and inserts or changes rows before it commits. The store call under test must
# wait for it and then see what it committed.


@asynccontextmanager
async def holding_user_lock(
    sessions: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    async with sessions.begin() as session:
        await session.execute(select(User.id).where(User.id == user_id).with_for_update())
        yield session


async def blocked_until_commit(task: "asyncio.Task[object]") -> None:
    await asyncio.sleep(0.5)
    assert not task.done(), "the store call did not wait for the user's lock"


async def _insert(session: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID) -> str:
    token_hash = uuid.uuid4().hex * 2
    token = contract.first_token(user_id, family_id, token_hash)
    await session.execute(insert(RefreshToken).values(**asdict(token)))
    return token_hash


async def _rotate(tokens: SqlRefreshTokenStore, token_hash: str) -> object:
    return await tokens.rotate(
        [token_hash], uuid.uuid4().hex * 2, now=contract.NOW, refresh_ttl_seconds=60
    )


async def test_reuse_revokes_a_token_inserted_while_it_waited(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    users, tokens = SqlUserStore(sessions), SqlRefreshTokenStore(sessions)
    user_id, _ = await contract.new_user(users)
    family_id, old = await contract.new_family(tokens, user_id)
    await _rotate(tokens, old)
    async with holding_user_lock(sessions, user_id) as session:
        newest = await _insert(session, user_id, family_id)
        reuse = asyncio.create_task(_rotate(tokens, old))
        await blocked_until_commit(reuse)
    assert isinstance(await reuse, Reused)
    assert isinstance(await _rotate(tokens, newest), Reused)


async def test_revoking_a_user_catches_a_token_inserted_while_it_waited(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    users, tokens = SqlUserStore(sessions), SqlRefreshTokenStore(sessions)
    user_id, _ = await contract.new_user(users)
    async with holding_user_lock(sessions, user_id) as session:
        newest = await _insert(session, user_id, uuid.uuid4())
        revoke = asyncio.create_task(tokens.revoke_user(user_id, now=contract.NOW))
        await blocked_until_commit(revoke)
    await revoke
    assert isinstance(await _rotate(tokens, newest), Reused)


async def test_a_login_verified_before_a_secret_rotation_starts_no_family(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    users, tokens = SqlUserStore(sessions), SqlRefreshTokenStore(sessions)
    user_id, _ = await contract.new_user(users)
    async with holding_user_lock(sessions, user_id) as session:
        # What `rotate-secret` does while the login is between verifying and inserting.
        await session.execute(
            update(User).where(User.id == user_id).values(client_secret_hash="p$" + "9" * 64)
        )
        first = contract.first_token(user_id, uuid.uuid4(), uuid.uuid4().hex * 2)
        login = asyncio.create_task(tokens.add(first, secret_hash=contract.SECRET_HASH))
        await blocked_until_commit(login)
    assert await login is False


async def test_revocation_contract(redis_address: tuple[str, int], backend_redis: Redis) -> None:
    """A subscriber (here the default user; the gateway's in slice 6) sees every message."""
    subscriber = _redis(redis_address, "default", DEFAULT_PASSWORD)
    pubsub = subscriber.pubsub()
    await pubsub.subscribe(REVOCATION_CHANNEL)
    await pubsub.get_message(timeout=5)  # the subscribe confirmation

    async def observed() -> list[Revocation]:
        received = []
        while message := await pubsub.get_message(ignore_subscribe_messages=True, timeout=2):
            data = json.loads(message["data"])
            received.append(Revocation(data["kind"], uuid.UUID(data["id"])))
        return received

    try:
        await contract.assert_publishes_revocations(
            RedisRevocationPublisher(backend_redis), observed
        )
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]  # untyped in redis-py
        await subscriber.aclose()


@pytest.mark.parametrize(
    "command",
    [
        ("PUBLISH", "xype:other", "x"),
        ("SUBSCRIBE", REVOCATION_CHANNEL),
        ("SET", "outside:key", "x"),
        ("FLUSHALL",),
        ("CONFIG", "GET", "*"),
    ],
    ids=["publish-elsewhere", "subscribe", "key-outside-prefix", "flushall", "config"],
)
async def test_backend_acl_allows_only_what_the_adapters_need(
    backend_redis: Redis, command: tuple[object, ...]
) -> None:
    with pytest.raises(NoPermissionError):
        await backend_redis.execute_command(*command)  # type: ignore[no-untyped-call]  # untyped in redis-py


@pytest.mark.parametrize(
    "body",
    ["return redis.call('SET', 'outside:key', 'x')", "return redis.call('CONFIG', 'GET', '*')"],
    ids=["key-outside-prefix", "config"],
)
async def test_a_loaded_script_is_held_to_the_same_acl(backend_redis: Redis, body: str) -> None:
    """SCRIPT LOAD + EVALSHA can run any script, but every call inside it is checked
    against the backend user's ACL, so a script gains no command, key or channel."""
    sha = await backend_redis.script_load(body)
    # CONFIG is refused in any script; the ACL refuses the rest.
    with pytest.raises(ResponseError, match=r"ACL failure in script|not allowed from script"):
        await backend_redis.evalsha(sha, 0)
