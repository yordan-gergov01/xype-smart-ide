"""`BusChunkPublisher` over the in-memory bus: a job lands on `index.chunks` with its
TTL; the bus is connected on the first publish and again after a broker failure."""

import uuid

import pytest
from support.bus import FakeBus
from support.contracts import merkle as contract

from backend.messaging.chunks import BusChunkPublisher
from common_lib.errors import DependencyUnavailableError
from common_lib.index_messages import ChunkJob, WireChunkRef, decode_job
from common_lib.messaging import CHUNKS
from common_lib.security import nonce_for

JOB = ChunkJob(
    user_id=uuid.uuid4(),
    team_id=uuid.uuid4(),
    key_id=uuid.uuid4(),
    message_id="m-1",
    request_id="r-1",
    field_number=3,
    chunk=WireChunkRef.of(contract.ref(uuid.uuid4(), contract.TREE[2])),
    nonce=nonce_for(1),
    ciphertext=b"c" * 32,
)


class Broken(FakeBus):
    async def publish(self, *args: object, **kwargs: object) -> None:
        raise DependencyUnavailableError("broker")


async def test_a_job_is_published_once_connected_and_again_after_a_failure() -> None:
    buses: list[FakeBus] = [Broken(), FakeBus()]
    connects = 0

    async def connect() -> FakeBus:
        nonlocal connects
        connects += 1
        return buses[connects - 1]

    publisher = BusChunkPublisher(connect)
    assert connects == 0  # nothing until the first upload
    with pytest.raises(DependencyUnavailableError):
        await publisher.publish(JOB, correlation_id="corr-1", ttl_ms=60_000)
    assert buses[0].closed
    await publisher.publish(JOB, correlation_id="corr-1", ttl_ms=60_000)
    [message] = buses[1].queues[CHUNKS]
    assert decode_job(message.body) == JOB
    assert (message.correlation_id, message.deadline_ms is not None) == ("corr-1", True)
    await publisher.close()
    assert connects == 2
    assert buses[1].closed
