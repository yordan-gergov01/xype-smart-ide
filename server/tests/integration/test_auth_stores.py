"""The auth, session and repository adapters meet the shared contracts against Postgres
(migrated, app role) and Redis (TLS, the backend's ACL user), as in
deploy/docker-compose.yml (ADR-0013, ADR-0014, ADR-0016)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace

import pytest
from redis.asyncio import Redis
from redis.exceptions import NoPermissionError, ResponseError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from support import containers
from support.contracts import auth as contract
from support.contracts import repositories as repository_contract
from support.database import Database, alembic, postgres
from support.session import assert_only_the_teams_repositories
from support.tls import ca_file

from backend.dal.auth import (
    REVOCATION_CHANNEL,
    RedisRateLimiter,
    RedisRevocationPublisher,
    RedisRevocationSubscriber,
    SqlRefreshTokenStore,
    SqlUserStore,
)
from backend.dal.repositories import SqlRepositoryStore
from backend.dal.session import SqlRepositoryAccess
from backend.services.auth import RateLimiter, RefreshTokenStore, UserStore
from common_lib.auth import Reused, Revocation, Rotated
from common_lib.errors import InvalidValueError
from common_lib.models import RefreshToken, Repository, RepositoryKey, User
from common_lib.models.connection import postgres_url
from common_lib.security import WrappedKey
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


async def test_revocation_contract(backend_redis: Redis) -> None:
    """Publisher and subscriber both as the backend's ACL user, as the gateway runs them."""

    async def settle() -> None:
        await asyncio.sleep(0.5)

    await contract.assert_hears_revocations(
        RedisRevocationPublisher(backend_redis),
        RedisRevocationSubscriber(backend_redis),
        settle=settle,
    )


async def test_the_subscriber_skips_what_is_not_a_revocation(
    redis_address: tuple[str, int], backend_redis: Redis
) -> None:
    other = _redis(redis_address, "default", DEFAULT_PASSWORD)
    revocations = RedisRevocationSubscriber(backend_redis).revocations()
    first = asyncio.ensure_future(anext(revocations))
    try:
        await asyncio.sleep(0.5)
        junk_messages = (
            "not json",
            f'{{"kind": "team", "id": "{uuid.uuid4()}"}}',
            '{"kind": "user"}',
            '{"kind": "user", "id": 5}',
            '{"kind": "user", "id": "not-a-uuid"}',
            "[1, 2]",
        )
        for junk in junk_messages:
            await other.publish(REVOCATION_CHANNEL, junk)
        valid = Revocation("user", uuid.uuid4())
        await RedisRevocationPublisher(backend_redis).publish(valid)
        assert await asyncio.wait_for(first, timeout=5) == valid
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await revocations.aclose()
        await other.aclose()


@pytest.mark.parametrize(
    "command",
    [
        ("PUBLISH", "xype:other", "x"),
        ("SUBSCRIBE", "xype:other"),
        ("PSUBSCRIBE", "xype:*"),
        ("SET", "outside:key", "x"),
        ("FLUSHALL",),
        ("CONFIG", "GET", "*"),
    ],
    ids=[
        "publish-elsewhere",
        "subscribe-elsewhere",
        "psubscribe",
        "key-outside-prefix",
        "flushall",
        "config",
    ],
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


async def test_repository_access_contract(sessions: async_sessionmaker[AsyncSession]) -> None:
    users = SqlUserStore(sessions)
    teams = (
        await users.create_team(f"repos-{uuid.uuid4().hex[:8]}"),
        await users.create_team(f"repos-{uuid.uuid4().hex[:8]}"),
    )

    async def register(team_id: uuid.UUID) -> uuid.UUID:
        values = {"team_id": team_id, "name": uuid.uuid4().hex, "hmac_key_id": "hk-1"}
        async with sessions.begin() as session:
            query = insert(Repository).values(**values).returning(Repository.id)
            repository_id: uuid.UUID = (await session.execute(query)).scalar_one()
        return repository_id

    await assert_only_the_teams_repositories(SqlRepositoryAccess(sessions), register, teams)


async def _two_teams(sessions: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    users = SqlUserStore(sessions)
    return (
        await users.create_team(f"keys-{uuid.uuid4().hex[:8]}"),
        await users.create_team(f"keys-{uuid.uuid4().hex[:8]}"),
    )


async def test_repository_store_meets_the_contract(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlRepositoryStore(sessions)
    await repository_contract.check_keys_round_trip_for_the_team_only(
        store, await _two_teams(sessions)
    )
    await repository_contract.check_a_name_is_unique_per_team(store, await _two_teams(sessions))


async def test_a_retired_key_is_not_issued_and_a_bad_wrap_is_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlRepositoryStore(sessions)
    mine, _ = await _two_teams(sessions)

    async def retire(repository_id: uuid.UUID) -> None:
        async with sessions.begin() as session:
            await session.execute(
                update(RepositoryKey)
                .where(RepositoryKey.repository_id == repository_id)
                .values(retired_at=RepositoryKey.created_at)
            )

    await repository_contract.check_a_retired_key_is_not_issued(store, retire, mine)
    short = repository_contract.new_repository(mine, "short")
    wrapped = short.wrapped_key
    bad = WrappedKey(wrapped.kek_id, wrapped.nonce, wrapped.ciphertext[:-1])
    with pytest.raises(InvalidValueError):
        await store.create(replace(short, wrapped_key=bad))
    async with sessions() as session:
        query = select(Repository.name).where(Repository.team_id == mine)
        assert set((await session.execute(query)).scalars()) == {"kept", "retired"}
