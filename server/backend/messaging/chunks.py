"""The `ChunkPublisher` on `index.chunks` (ADR-0015, slice 8c3b): one bus for the whole
process, connected on the first upload (the backend starts without a broker) and again
after it failed. The robust connection reconnects on its own in between."""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable

from common_lib.errors import DependencyUnavailableError
from common_lib.index_messages import ChunkJob, encode
from common_lib.messaging import CHUNKS, MessageBus


class BusChunkPublisher:
    def __init__(self, connect: Callable[[], Awaitable[MessageBus]]) -> None:
        self._connect = connect
        self._bus: MessageBus | None = None
        self._lock = asyncio.Lock()

    async def publish(self, job: ChunkJob, *, correlation_id: str, ttl_ms: int) -> None:
        bus = await self._connected()
        try:
            await bus.publish(
                CHUNKS,
                encode(job),
                message_id=str(uuid.uuid4()),
                correlation_id=correlation_id,
                ttl_ms=ttl_ms,
            )
        except DependencyUnavailableError:
            await self._drop(bus)
            raise

    async def close(self) -> None:
        if self._bus is not None:
            await self._drop(self._bus)

    async def _connected(self) -> MessageBus:
        async with self._lock:
            if self._bus is None:
                self._bus = await self._connect()
            return self._bus

    async def _drop(self, bus: MessageBus) -> None:
        async with self._lock:
            if self._bus is bus:
                self._bus = None
        with contextlib.suppress(DependencyUnavailableError, OSError):
            await bus.close()
