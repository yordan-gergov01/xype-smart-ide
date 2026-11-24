"""`RecordingBus`: the in-memory bus (`FakeBus`, held to the bus contract) that also
records the TTL of each publish and can refuse like a broker after `fail_after`
publishes. `ChunkUploads` tests use the real `BusChunkPublisher` over it, so the
publisher needs no fake of its own."""

from backend.messaging.chunks import BusChunkPublisher
from common_lib.errors import DependencyUnavailableError
from common_lib.index_messages import ChunkJob, decode_job
from common_lib.messaging import CHUNKS
from support.bus import FakeBus


class RecordingBus(FakeBus):
    def __init__(self) -> None:
        super().__init__()
        self.ttls: list[int | None] = []
        self.fail_after: int | None = None  # publishes that succeed before the refusal

    async def publish(
        self,
        queue: str,
        body: bytes,
        *,
        message_id: str,
        correlation_id: str,
        ttl_ms: int | None = None,
    ) -> None:
        if self.fail_after is not None and len(self.ttls) >= self.fail_after:
            raise DependencyUnavailableError("broker")
        await super().publish(
            queue, body, message_id=message_id, correlation_id=correlation_id, ttl_ms=ttl_ms
        )
        self.ttls.append(ttl_ms)

    @property
    def published(self) -> list[tuple[ChunkJob, int | None]]:
        jobs = [decode_job(message.body) for message in self.queues[CHUNKS]]
        return list(zip(jobs, self.ttls, strict=True))

    def publisher(self) -> BusChunkPublisher:
        # The publisher closes the bus after a failure and connects again: this hands it
        # the same bus, which still publishes after `close()` (a reconnect, not a refusal).
        async def connect() -> "RecordingBus":
            return self

        return BusChunkPublisher(connect)
