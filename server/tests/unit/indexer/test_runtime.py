"""The indexer's runtime: health and metrics answers over HTTP."""

import asyncio

from prometheus_client import CollectorRegistry

from indexer.health import HealthServer
from indexer.metrics import IndexerMetrics


def test_health_ready_and_metrics_answer() -> None:
    registry, ready = CollectorRegistry(), [False]
    IndexerMetrics.create(registry)
    server = HealthServer(registry, lambda: ready[0])
    assert server.answer("/healthz").startswith(b"HTTP/1.1 200")
    assert server.answer("/readyz").startswith(b"HTTP/1.1 503")
    ready[0] = True
    assert server.answer("/readyz").startswith(b"HTTP/1.1 200")
    assert b'xype_indexer_chunks_total{outcome="embedded"} 0.0' in server.answer("/metrics")
    assert server.answer("/anything").startswith(b"HTTP/1.1 404")


async def test_the_health_server_answers_over_tcp() -> None:
    server = await HealthServer(CollectorRegistry(), lambda: True).start("127.0.0.1:0")
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /readyz HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        assert (await reader.read()).startswith(b"HTTP/1.1 200")
        writer.close()
    finally:
        server.close()
