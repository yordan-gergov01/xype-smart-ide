"""`RabbitBus` against RabbitMQ with the compose file's definitions (TLS, the index
topology, one user per service): the shared contract as the backend, and least
privilege: each user reads only its own queue and can declare nothing."""

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from support import containers
from support.contracts import bus as contract
from support.tls import ca_file
from testcontainers.core.container import DockerContainer

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging import CHUNKS, VECTORS, RabbitBus, connect_bus
from common_lib.settings import RabbitMQSettings

pytestmark = pytest.mark.integration

PASSWORD = "test-only-broker"


@pytest.fixture(scope="module")
def broker() -> Iterator[DockerContainer]:
    env = {
        "RABBITMQ_VHOST": "xype",
        "RABBITMQ_BACKEND_USER": "backend",
        "RABBITMQ_BACKEND_PASSWORD": PASSWORD,
        "RABBITMQ_INDEXER_USER": "indexer",
        "RABBITMQ_INDEXER_PASSWORD": PASSWORD,
        # Short delays, so a test sees every retry.
        "RABBITMQ_RETRY_1_MS": "300",
        "RABBITMQ_RETRY_2_MS": "600",
        "RABBITMQ_RETRY_3_MS": "900",
    }
    with containers.rabbitmq(env) as container:
        yield container


def _settings(container: DockerContainer, user: str) -> RabbitMQSettings:
    return RabbitMQSettings.model_validate(
        {
            "tls_ca_file": ca_file(),
            "rabbitmq_host": container.get_container_host_ip(),
            "rabbitmq_port": int(container.get_exposed_port(5671)),
            "rabbitmq_vhost": "xype",
            "rabbitmq_user": user,
            "rabbitmq_password": PASSWORD,
        }
    )


@pytest.fixture
async def buses(broker: DockerContainer) -> AsyncIterator[tuple[RabbitBus, RabbitBus]]:
    """The indexer's bus (it publishes results) and the backend's (it reads them)."""
    indexer = await connect_bus(_settings(broker, "indexer"))
    backend = await connect_bus(_settings(broker, "backend"))
    yield indexer, backend
    await indexer.close()
    await backend.close()


def _depths(container: DockerContainer) -> dict[str, int]:
    """Ready plus unacknowledged messages per queue, from the broker itself."""
    command = ["rabbitmqctl", "list_queues", "-p", "xype", "name", "messages", "--quiet"]
    out = container.exec(command).output.decode()
    rows = (line.split() for line in out.splitlines() if line.strip())
    return {row[0]: int(row[1]) for row in rows if len(row) == 2 and row[1].isdigit()}


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_rabbitmq_meets_the_contract(
    broker: DockerContainer, buses: tuple[RabbitBus, RabbitBus], check: contract.Check
) -> None:
    async def depth(queue: str) -> int:
        return (await asyncio.to_thread(_depths, broker)).get(queue, 0)

    await check(*buses, depth)


async def test_each_service_reads_only_its_own_queue(broker: DockerContainer) -> None:
    async def nothing(_: object) -> None:
        return None

    for user, foreign in (("backend", CHUNKS), ("indexer", VECTORS)):
        bus = await connect_bus(_settings(broker, user))
        try:
            with pytest.raises(DependencyUnavailableError):
                await asyncio.wait_for(bus.consume(foreign, nothing, prefetch=1), timeout=15)
        finally:
            await bus.close()


async def test_each_service_publishes_only_where_its_role_needs(
    buses: tuple[RabbitBus, RabbitBus],
) -> None:
    indexer, backend = buses
    for bus, foreign in ((backend, VECTORS), (indexer, CHUNKS)):
        with pytest.raises(DependencyUnavailableError):
            await bus.publish(foreign, b"{}", message_id="m", correlation_id="c")


async def test_the_topology_is_the_definitions(broker: DockerContainer) -> None:
    depths = await asyncio.to_thread(_depths, broker)
    families = {
        name
        for queue in (CHUNKS, VECTORS)
        for name in (queue, f"{queue}.dlq", *(f"{queue}.retry.{n}" for n in (1, 2, 3)))
    }
    assert set(depths) == families
