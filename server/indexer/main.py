"""Composition root of the indexer worker: `python -m indexer.main` (ADR-0009, ADR-0015).

Builds the key store (its `GET`-only ACL user), the scrubber, the embedder and the bus,
then consumes `index.chunks` until SIGTERM. A broker that goes away ends the consumer;
it starts again with a growing wait, and `/readyz` says not ready meanwhile.
"""

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable

import structlog
from prometheus_client import CollectorRegistry

from common_lib.domain import ModelRef
from common_lib.embeddings.ollama import OllamaEmbedder
from common_lib.errors import DependencyUnavailableError
from common_lib.keystore import RedisKeyStore, connect_keystore, kek_ring
from common_lib.logging_config import configure_logging
from common_lib.messaging import CHUNKS, MessageBus, connect_bus
from common_lib.security import SecretScrubber, load_rules
from common_lib.settings import load_settings
from common_lib.tracing import configure_tracing
from indexer.handler import ChunkIndexer
from indexer.health import HealthServer, Readiness
from indexer.metrics import IndexerMetrics
from indexer.settings import IndexerSettings

SERVICE_NAME = "indexer"
# Waits between consumer restarts: 1 s, 2 s, ... up to 30 s.
_RESTART_MIN_S, _RESTART_MAX_S = 1.0, 30.0

logger = structlog.get_logger(__name__)


async def consume_forever(
    connect: Callable[[], Awaitable[MessageBus]],
    run: Callable[[MessageBus], Awaitable[None]],
    readiness: Readiness,
) -> None:
    """Connects and consumes until cancelled; a broker failure waits and starts again."""
    wait = _RESTART_MIN_S
    while True:
        bus: MessageBus | None = None
        try:
            bus = await connect()
            readiness.consuming = True
            wait = _RESTART_MIN_S
            await run(bus)
        except DependencyUnavailableError:
            logger.warning("indexer.consumer.restarting", wait_s=wait)
        finally:
            readiness.consuming = False
            if bus is not None:
                with contextlib.suppress(DependencyUnavailableError, OSError):
                    await bus.close()
        await asyncio.sleep(wait)
        wait = min(wait * 2, _RESTART_MAX_S)


async def watch(readiness: Readiness, stop: Callable[[], object], *, every_s: float = 5.0) -> None:
    """Stops the worker when every scan thread has been stuck too long (`healthy`): the
    process then exits and the container restarts, its unacknowledged chunks going back
    to the queue. A scan cannot be killed in its thread (ADR-0015, slice 8b)."""
    # Health turns false with the passing of time, not on an event: polled.
    while readiness.healthy():  # noqa: ASYNC110
        await asyncio.sleep(every_s)
    logger.error("indexer.scans_stuck")
    stop()


async def run(settings: IndexerSettings) -> None:
    tracer_provider = configure_tracing(
        service=SERVICE_NAME, otlp_endpoint=settings.otel_exporter_otlp_endpoint
    )
    keystore = connect_keystore(settings)
    keys = RedisKeyStore(keystore, kek_ring(settings))
    scrubber = SecretScrubber(load_rules(settings.scrub_rules_file))
    embedder = OllamaEmbedder(
        model=ModelRef(settings.embedding_model_id, settings.embedding_model_version),
        base_url=settings.ollama_base_url,
        timeout_s=settings.embedding_timeout_s,
    )
    registry, readiness = CollectorRegistry(), Readiness()
    metrics = IndexerMetrics.create(registry)
    tracer = tracer_provider.get_tracer(SERVICE_NAME)
    server = await HealthServer(registry, readiness.ready, readiness.healthy).start(
        settings.metrics_listen_address
    )

    async def consume(bus: MessageBus) -> None:
        indexer = ChunkIndexer(
            keys, scrubber, embedder, bus, metrics=metrics, tracer=tracer, readiness=readiness
        )
        await bus.consume(CHUNKS, indexer.handle, prefetch=settings.prefetch)

    logger.info("indexer.started")
    worker = asyncio.create_task(consume_forever(lambda: connect_bus(settings), consume, readiness))
    watcher = asyncio.create_task(watch(readiness, worker.cancel))
    try:
        await worker
    finally:
        watcher.cancel()
        server.close()
        await embedder.aclose()
        await keystore.aclose()
        await asyncio.to_thread(tracer_provider.shutdown)
        logger.info("indexer.stopped")
        if not readiness.healthy():
            # Stuck scan threads would keep a normal exit waiting for them forever.
            os._exit(1)


async def _main() -> None:
    settings = load_settings(IndexerSettings)
    configure_logging(
        service=SERVICE_NAME, level=settings.log_level, log_format=settings.log_format
    )
    task = asyncio.create_task(run(settings))
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not on Windows
            loop.add_signal_handler(stop, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await task


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
