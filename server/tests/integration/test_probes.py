"""The real readiness probes meet the probe contract against real Postgres, Redis and
RabbitMQ, configured as in deploy/docker-compose.yml: TLS only, verified against the
dev CA (ADR-0036). Plaintext and servers from another CA are refused."""

from collections.abc import Iterator
from contextlib import AsyncExitStack

import aio_pika
import pytest
from aio_pika.exceptions import AMQPError
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from support import containers
from support.backend import make_settings
from support.probe_contract import assert_reachable, assert_unreachable
from support.tls import other_ca_file

from backend.core.connections import create_database, create_redis_client
from backend.core.settings import BackendSettings
from backend.dal.health import DatabaseProbe, RedisProbe
from backend.messaging.health import BrokerProbe
from common_lib.models.connection import postgres_url

pytestmark = pytest.mark.integration

TEST_PASSWORD = "test-only-not-a-secret"
# Nothing listens on port 1 of the loopback interface.
CLOSED = {"host": "127.0.0.1", "port": 1}


def _with_other_ca(settings: BackendSettings) -> BackendSettings:
    return settings.model_copy(update={"tls_ca_file": other_ca_file()})


@pytest.fixture(scope="module")
def postgres() -> Iterator[BackendSettings]:
    env = {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": TEST_PASSWORD, "POSTGRES_DB": "xype"}
    with containers.postgres(env) as container:
        yield make_settings(
            db_host=container.get_container_host_ip(),
            db_port=int(container.get_exposed_port(5432)),
            db_user="app",
            db_password=TEST_PASSWORD,
            readiness_timeout_seconds=5,
        )


@pytest.fixture(scope="module")
def redis() -> Iterator[BackendSettings]:
    env = {
        "REDIS_PASSWORD": "test-only-default",
        "REDIS_BACKEND_USER": "backend",
        "REDIS_BACKEND_PASSWORD": TEST_PASSWORD,
    }
    with containers.redis(env) as container:
        yield make_settings(
            redis_host=container.get_container_host_ip(),
            redis_port=int(container.get_exposed_port(6379)),
            redis_user="backend",
            redis_password=TEST_PASSWORD,
            readiness_timeout_seconds=5,
        )


@pytest.fixture(scope="module")
def rabbitmq() -> Iterator[BackendSettings]:
    env = {
        "RABBITMQ_VHOST": "xype",
        "RABBITMQ_BACKEND_USER": "backend",
        "RABBITMQ_BACKEND_PASSWORD": TEST_PASSWORD,
        "RABBITMQ_INDEXER_USER": "indexer",
        "RABBITMQ_INDEXER_PASSWORD": TEST_PASSWORD,
    }
    with containers.rabbitmq(env) as container:
        yield make_settings(
            rabbitmq_host=container.get_container_host_ip(),
            rabbitmq_port=int(container.get_exposed_port(5671)),
            rabbitmq_user="backend",
            rabbitmq_password=TEST_PASSWORD,
            readiness_timeout_seconds=5,
        )


async def test_database_probe_meets_the_contract(postgres: BackendSettings) -> None:
    wrong = postgres.model_copy(update={"db_password": SecretStr("wrong")})
    async with AsyncExitStack() as stack:
        good, bad, foreign = (
            create_database(s) for s in (postgres, wrong, _with_other_ca(postgres))
        )
        for database in (good, bad, foreign):
            stack.push_async_callback(database.dispose)
        await assert_reachable(DatabaseProbe(good.write_engine))
        await assert_unreachable(DatabaseProbe(bad.write_engine))
        await assert_unreachable(DatabaseProbe(foreign.write_engine))


async def test_database_refuses_plaintext(postgres: BackendSettings) -> None:
    engine = create_async_engine(postgres_url(postgres), connect_args={"ssl": False})
    try:
        with pytest.raises(DBAPIError, match="no encryption"):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def test_redis_probe_meets_the_contract(redis: BackendSettings) -> None:
    closed = redis.model_copy(update={"redis_host": CLOSED["host"], "redis_port": CLOSED["port"]})
    async with AsyncExitStack() as stack:
        good, bad, foreign = (
            create_redis_client(s) for s in (redis, closed, _with_other_ca(redis))
        )
        for client in (good, bad, foreign):
            stack.push_async_callback(client.aclose)
        await assert_reachable(RedisProbe(good))
        await assert_unreachable(RedisProbe(bad))
        await assert_unreachable(RedisProbe(foreign))


async def test_redis_refuses_plaintext(redis: BackendSettings) -> None:
    plain = Redis(
        host=redis.redis_host,
        port=redis.redis_port,
        username=redis.redis_user,
        password=redis.redis_password.get_secret_value(),
        socket_timeout=5,
    )
    try:
        with pytest.raises((RedisConnectionError, OSError)):
            await plain.ping()
    finally:
        await plain.aclose()


async def test_broker_probe_meets_the_contract(rabbitmq: BackendSettings) -> None:
    await assert_reachable(BrokerProbe(rabbitmq))
    wrong = rabbitmq.model_copy(update={"rabbitmq_vhost": "no-such-vhost"})
    await assert_unreachable(BrokerProbe(wrong))
    closed = rabbitmq.model_copy(
        update={"rabbitmq_host": CLOSED["host"], "rabbitmq_port": CLOSED["port"]}
    )
    await assert_unreachable(BrokerProbe(closed))
    await assert_unreachable(BrokerProbe(_with_other_ca(rabbitmq)))


async def test_broker_refuses_plaintext(rabbitmq: BackendSettings) -> None:
    # A plaintext client never gets a connection object to close.
    with pytest.raises((AMQPError, OSError)):
        await aio_pika.connect(
            host=rabbitmq.rabbitmq_host,
            port=rabbitmq.rabbitmq_port,
            virtualhost=rabbitmq.rabbitmq_vhost,
            login=rabbitmq.rabbitmq_user,
            password=rabbitmq.rabbitmq_password.get_secret_value(),
            timeout=5,
        )
