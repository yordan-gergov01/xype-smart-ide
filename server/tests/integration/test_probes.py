"""The real readiness probes meet the probe contract against real Postgres, Redis and
RabbitMQ (testcontainers, the same pinned images as deploy/docker-compose.yml)."""

from collections.abc import Iterator
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr
from support.backend import make_settings
from support.probe_contract import assert_reachable, assert_unreachable
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from backend.core.connections import create_database, create_redis_client
from backend.core.settings import BackendSettings
from backend.dal.health import DatabaseProbe, RedisProbe
from backend.messaging.health import BrokerProbe

pytestmark = pytest.mark.integration

# One pin for each image: the compose file's.
_COMPOSE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml").read_text("utf-8")
)
POSTGRES_IMAGE = _COMPOSE["services"]["postgres"]["image"]
REDIS_IMAGE = _COMPOSE["services"]["redis"]["image"]
RABBITMQ_IMAGE = _COMPOSE["services"]["rabbitmq"]["image"]
TEST_PASSWORD = "test-only-not-a-secret"
# Nothing listens on port 1 of the loopback interface.
CLOSED = {"host": "127.0.0.1", "port": 1}


@pytest.fixture(scope="module")
def postgres() -> Iterator[BackendSettings]:
    with PostgresContainer(
        POSTGRES_IMAGE, username="app", password=TEST_PASSWORD, dbname="xype", driver=None
    ) as container:
        yield make_settings(
            db_host=container.get_container_host_ip(),
            db_port=int(container.get_exposed_port(5432)),
            db_user="app",
            db_password=TEST_PASSWORD,
            readiness_timeout_seconds=5,
        )


@pytest.fixture(scope="module")
def redis() -> Iterator[BackendSettings]:
    with RedisContainer(REDIS_IMAGE, password=TEST_PASSWORD) as container:
        yield make_settings(
            redis_host=container.get_container_host_ip(),
            redis_port=int(container.get_exposed_port(6379)),
            redis_user="default",
            redis_password=TEST_PASSWORD,
            readiness_timeout_seconds=5,
        )


@pytest.fixture(scope="module")
def rabbitmq() -> Iterator[BackendSettings]:
    container = (
        DockerContainer(RABBITMQ_IMAGE)
        .with_env("RABBITMQ_DEFAULT_USER", "backend")
        .with_env("RABBITMQ_DEFAULT_PASS", TEST_PASSWORD)
        .with_env("RABBITMQ_DEFAULT_VHOST", "xype")
        .with_exposed_ports(5672)
        .waiting_for(LogMessageWaitStrategy("Server startup complete").with_startup_timeout(120))
    )
    with container:
        yield make_settings(
            rabbitmq_host=container.get_container_host_ip(),
            rabbitmq_port=int(container.get_exposed_port(5672)),
            rabbitmq_user="backend",
            rabbitmq_password=TEST_PASSWORD,
            readiness_timeout_seconds=5,
        )


async def test_database_probe_meets_the_contract(postgres: BackendSettings) -> None:
    wrong = postgres.model_copy(update={"db_password": SecretStr("wrong")})
    async with AsyncExitStack() as stack:
        good, bad = create_database(postgres), create_database(wrong)
        stack.push_async_callback(good.dispose)
        stack.push_async_callback(bad.dispose)
        await assert_reachable(DatabaseProbe(good.write_engine))
        await assert_unreachable(DatabaseProbe(bad.write_engine))


async def test_redis_probe_meets_the_contract(redis: BackendSettings) -> None:
    closed = redis.model_copy(update={"redis_host": CLOSED["host"], "redis_port": CLOSED["port"]})
    async with AsyncExitStack() as stack:
        good, bad = create_redis_client(redis), create_redis_client(closed)
        stack.push_async_callback(good.aclose)
        stack.push_async_callback(bad.aclose)
        await assert_reachable(RedisProbe(good))
        await assert_unreachable(RedisProbe(bad))


async def test_broker_probe_meets_the_contract(rabbitmq: BackendSettings) -> None:
    await assert_reachable(BrokerProbe(rabbitmq))
    wrong = rabbitmq.model_copy(update={"rabbitmq_vhost": "no-such-vhost"})
    await assert_unreachable(BrokerProbe(wrong))
    closed = rabbitmq.model_copy(
        update={"rabbitmq_host": CLOSED["host"], "rabbitmq_port": CLOSED["port"]}
    )
    await assert_unreachable(BrokerProbe(closed))
