"""Composition root of the indexer worker: `python -m indexer.main` (ADR-0009, ADR-0015).

Builds the key store (its `GET`-only ACL user), the scan processes, the embedder and the
bus, then consumes `index.chunks` until SIGTERM. A broker that goes away ends the consumer;
it starts again with a growing wait, and `/readyz` says not ready meanwhile.
"""

import asyncio
import contextlib
import signal

import structlog
from prometheus_client import CollectorRegistry

from common_lib.domain import ModelRef
from common_lib.embeddings.ollama import OllamaEmbedder
from common_lib.keystore import RedisKeyStore, connect_keystore, kek_ring
from common_lib.logging_config import configure_logging
from common_lib.messaging import CHUNKS, MessageBus, connect_bus, consume_forever
from common_lib.settings import load_settings
from common_lib.tracing import configure_tracing
from indexer.handler import ChunkIndexer
from indexer.health import HealthServer, Readiness
from indexer.metrics import IndexerMetrics
from indexer.scan import ProcessScanner
from indexer.settings import IndexerSettings

SERVICE_NAME = "indexer"

logger = structlog.get_logger(__name__)


async def run(settings: IndexerSettings) -> None:
    tracer_provider = configure_tracing(
        service=SERVICE_NAME, otlp_endpoint=settings.otel_exporter_otlp_endpoint
    )
    keystore = connect_keystore(settings)
    keys = RedisKeyStore(keystore, kek_ring(settings))
    scanner = ProcessScanner(
        settings.scrub_rules_file, workers=settings.scan_workers, timeout_s=settings.scan_timeout_s
    )
    await scanner.start()
    embedder = OllamaEmbedder(
        model=ModelRef(settings.embedding_model_id, settings.embedding_model_version),
        base_url=settings.ollama_base_url,
        timeout_s=settings.embedding_timeout_s,
    )
    registry, readiness = CollectorRegistry(), Readiness()
    metrics = IndexerMetrics.create(registry)
    tracer = tracer_provider.get_tracer(SERVICE_NAME)
    server = await HealthServer(registry, readiness.ready).start(settings.metrics_listen_address)

    async def consume(bus: MessageBus) -> None:
        indexer = ChunkIndexer(
            keys, scanner, embedder, bus, metrics=metrics, tracer=tracer, readiness=readiness
        )
        await bus.consume(CHUNKS, indexer.handle, prefetch=settings.prefetch)

    logger.info("indexer.started")
    worker = asyncio.create_task(
        consume_forever(lambda: connect_bus(settings), consume, readiness.set_consuming)
    )
    try:
        await worker
    finally:
        server.close()
        await scanner.close()
        await embedder.aclose()
        await keystore.aclose()
        await asyncio.to_thread(tracer_provider.shutdown)
        logger.info("indexer.stopped")


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
