"""Composition root of the backend: `uvicorn backend.main:create_app --factory`.

The only place where adapters are built and wired (ADR-0009). Nothing connects at
startup; readiness reports which dependency is missing.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer
from prometheus_client import CollectorRegistry
from redis.asyncio import Redis

from backend.api.auth import router as auth_router
from backend.api.errors import register_error_handlers
from backend.api.health import router as health_router
from backend.api.middleware import HttpMetrics, RequestContextMiddleware, UnhandledErrorMiddleware
from backend.core.auth import auth_dependencies, auth_service, session_key_service
from backend.core.connections import (
    Database,
    create_database,
    create_keystore_client,
    create_redis_client,
)
from backend.core.session import session_gate, session_stream, sync_factory
from backend.core.settings import BackendSettings
from backend.dal.auth import RedisRevocationSubscriber
from backend.dal.health import DatabaseProbe, RedisProbe
from backend.dal.indexing import PgMerkleIndex
from backend.dal.session import SqlRepositoryAccess
from backend.grpc import SessionStream, create_grpc_server
from backend.messaging.health import BrokerProbe
from backend.messaging.vectors import ResultMetrics, VectorResults
from backend.services.auth import AuthDependencies, RevocationSubscriber
from backend.services.health import DependencyProbe, HealthService
from backend.services.indexing import MerkleIndex
from backend.services.session import RepositoryAccess, StreamRegistry, listen_for_revocations
from common_lib.clock import Clock, SystemClock
from common_lib.logging_config import configure_logging
from common_lib.messaging import VECTORS, MessageBus, connect_bus, consume_forever
from common_lib.settings import load_settings
from common_lib.tracing import configure_tracing

SERVICE_NAME = "backend"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Indexing:
    """The Merkle index and the broker connection its results arrive on (`index.vectors`);
    tests pass fakes, the defaults are Postgres and the backend's broker user."""

    merkle: MerkleIndex | None = None
    bus: Callable[[], Awaitable[MessageBus]] | None = None


REAL_INDEXING = Indexing()


def _default_probes(
    settings: BackendSettings, database: Database, redis: Redis, keystore: Redis
) -> tuple[DependencyProbe, ...]:
    """The primary, the read replica when there is one, both Redis and the broker."""
    probes: list[DependencyProbe] = [DatabaseProbe(database.write_engine)]
    if database.read_engine is not database.write_engine:
        probes.append(DatabaseProbe(database.read_engine, name="database_read"))
    return (
        *probes,
        RedisProbe(redis),
        RedisProbe(keystore, name="keystore"),
        BrokerProbe(settings),
    )


def _observability(settings: BackendSettings) -> TracerProvider:
    """Logs and traces first, so everything after them is observed."""
    configure_logging(
        service=SERVICE_NAME, level=settings.log_level, log_format=settings.log_format
    )
    return configure_tracing(
        service=SERVICE_NAME, otlp_endpoint=settings.otel_exporter_otlp_endpoint
    )


def create_app(
    settings: BackendSettings | None = None,
    *,
    probes: Sequence[DependencyProbe] | None = None,
    auth: AuthDependencies | None = None,
    repositories: RepositoryAccess | None = None,
    revocations: RevocationSubscriber | None = None,
    clock: Clock | None = None,
    indexing: Indexing = REAL_INDEXING,
) -> FastAPI:
    """Build the app. Settings come from the environment unless given; tests pass fakes."""
    settings = settings or load_settings(BackendSettings)
    tracer_provider = _observability(settings)
    database = create_database(settings)
    redis, keystore = create_redis_client(settings), create_keystore_client(settings)
    if probes is None:
        probes = _default_probes(settings, database, redis, keystore)
    clock = clock or SystemClock()
    auth = auth or auth_dependencies(settings, database, redis, keystore)
    repositories = repositories or SqlRepositoryAccess(database.read_sessions)
    revocations = revocations or RedisRevocationSubscriber(redis)
    registry, tracer = CollectorRegistry(), tracer_provider.get_tracer(SERVICE_NAME)
    authentication = auth_service(settings, auth, clock)
    gate = session_gate(settings, authentication, auth, repositories, clock)
    streams = StreamRegistry()
    merkle = indexing.merkle or PgMerkleIndex(database.write_sessions)
    syncs = sync_factory(settings, merkle, clock)
    stream = session_stream(
        gate, streams, syncs=syncs, clock=clock, metrics=registry, tracer=tracer
    )
    closers = (database.dispose, redis.aclose, keystore.aclose)
    consumer = _results_consumer(settings, indexing.bus, merkle, registry, tracer)
    app = FastAPI(
        title="XYPE backend",
        lifespan=_lifespan(
            settings, tracer_provider, closers, stream, streams, (revocations, consumer)
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.metrics_registry, app.state.tracer_provider = registry, tracer_provider
    app.state.health_service = HealthService(
        probes, timeout_seconds=settings.readiness_timeout_seconds
    )
    app.state.auth_service, app.state.session_stream = authentication, stream
    app.state.session_key_service = session_key_service(settings, auth, clock)
    _mount(app, tracer, HttpMetrics(registry))
    return app


def _results_consumer(
    settings: BackendSettings,
    connect: Callable[[], Awaitable[MessageBus]] | None,
    merkle: MerkleIndex,
    registry: CollectorRegistry,
    tracer: Tracer,
) -> Callable[[], Coroutine[Any, Any, None]]:  # reason: Any - a coroutine's send type
    """Consumes `index.vectors` as the backend's broker user, again after an outage; only
    vectors of the backend's embedding model are settled."""
    connect = connect or (lambda: connect_bus(settings))
    metrics = ResultMetrics.create(registry)
    results = VectorResults(merkle, model=settings.embedding_model, metrics=metrics, tracer=tracer)

    async def consume(bus: MessageBus) -> None:
        await bus.consume(VECTORS, results.handle, prefetch=settings.index_results_prefetch)

    def report(consuming: bool) -> None:
        logger.info("index.results.consumer", consuming=consuming)

    async def run() -> None:
        await consume_forever(connect, consume, report)

    return run


def _lifespan(
    settings: BackendSettings,
    tracer_provider: TracerProvider,
    closers: Sequence[Callable[[], Awaitable[object]]],
    stream: SessionStream,
    streams: StreamRegistry,
    background: tuple[RevocationSubscriber, Callable[[], Coroutine[Any, Any, None]]],
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Starts the Session stream server, the revocation listener and the `index.vectors`
    consumer. On shutdown, in order: every stream is told to reconnect elsewhere (drain),
    the server gives them the grace period, the consumer and the listener stop, the
    stores close, the last spans are flushed."""
    revocations, consume_results = background

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("backend.started")
        # Each step runs even if an earlier one fails (exit stack: last pushed, first run).
        async with AsyncExitStack() as stack:
            # The final span flush is a blocking network call: off the event loop.
            stack.push_async_callback(asyncio.to_thread, tracer_provider.shutdown)
            for close in closers:
                stack.push_async_callback(close)
            listener = asyncio.create_task(listen_for_revocations(revocations, streams))
            listener.add_done_callback(_report_early_end)
            stack.push_async_callback(_stop, listener)
            consumer = asyncio.create_task(consume_results())
            consumer.add_done_callback(_report_early_end)
            stack.push_async_callback(_stop, consumer)
            server, app.state.grpc_port = create_grpc_server(settings, stream)
            await server.start()
            stack.push_async_callback(server.stop, settings.stream_shutdown_grace_s)
            stack.callback(streams.drain)
            yield
        logger.info("backend.stopped")

    return lifespan


def _report_early_end(task: asyncio.Task[None]) -> None:
    """The background tasks only end by cancellation; anything else (streams now close at
    their next message only, or results wait in their queue) is logged, the type only,
    as soon as it happens."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("backend.background_failed", error=type(task.exception()).__name__)


async def _stop(task: asyncio.Task[None]) -> None:
    """Cancels a background task and collects its end, so no error goes unretrieved."""
    task.cancel()
    (result,) = await asyncio.gather(task, return_exceptions=True)
    if isinstance(result, Exception):
        logger.error("backend.task_failed", error=type(result).__name__)


def _mount(app: FastAPI, tracer: Tracer, metrics: HttpMetrics) -> None:
    """Routes, error handlers and middleware."""
    app.include_router(health_router)
    app.include_router(auth_router)
    register_error_handlers(app)
    # Starlette runs the last added middleware first, so this list is innermost first.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(RequestContextMiddleware, tracer=tracer, metrics=metrics)
