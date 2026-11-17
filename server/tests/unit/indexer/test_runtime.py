"""The indexer's runtime: health and metrics answers, and the consumer that starts again
after the broker goes away and reports itself not ready meanwhile."""

import asyncio
import contextlib

import pytest
from prometheus_client import CollectorRegistry
from support.bus import FakeBus

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging import MessageBus
from indexer import main
from indexer.health import HealthServer, Readiness
from indexer.main import consume_forever, watch
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
    stuck = HealthServer(registry, lambda: True, lambda: False)
    assert stuck.answer("/healthz").startswith(b"HTTP/1.1 503")


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


async def test_the_consumer_starts_again_after_the_broker_went_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_RESTART_MIN_S", 0.01)
    readiness, buses, runs = Readiness(), [FakeBus(), FakeBus()], 0
    states: list[bool] = []
    restarted = asyncio.Event()

    async def connect() -> MessageBus:
        if not buses:
            raise DependencyUnavailableError("broker")
        return buses.pop(0)

    async def run(_: MessageBus) -> None:
        nonlocal runs
        runs += 1
        states.append(readiness.consuming)
        if runs == 2:
            restarted.set()
        if runs < 3:
            raise DependencyUnavailableError("broker")
        await asyncio.Event().wait()  # consumes until cancelled

    task = asyncio.create_task(consume_forever(connect, run, readiness))
    await asyncio.wait_for(restarted.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert states[:2] == [True, True]
    assert not readiness.consuming


async def test_the_watchdog_stops_the_worker_when_scans_stay_stuck() -> None:
    readiness = Readiness(scans_full_since=0.0)  # stuck since long ago
    stopped = asyncio.Event()
    await asyncio.wait_for(watch(readiness, stopped.set, every_s=0.01), timeout=5)
    assert stopped.is_set()
    healthy = Readiness()
    task = asyncio.create_task(watch(healthy, stopped.clear, every_s=0.01))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
