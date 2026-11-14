"""Composition root of the backend: `uvicorn backend.main:create_app --factory`.

The only place where adapters are built and wired (ADR-0009). Nothing connects at
startup; readiness reports which dependency is missing.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

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
from backend.services.auth import AuthDependencies, RevocationSubscriber
from backend.services.health import DependencyProbe, HealthService
from backend.services.indexing import MerkleIndex
from backend.services.session import RepositoryAccess, StreamRegistry, listen_for_revocations
from common_lib.clock import Clock, SystemClock
from common_lib.logging_config import configure_logging
from common_lib.settings import load_settings
from common_lib.tracing import configure_tracing

SERVICE_NAME = "backend"

logger = structlog.get_logger(__name__)


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
    merkle: MerkleIndex | None = None,
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
    merkle = merkle or PgMerkleIndex(database.write_sessions)
    registry, tracer = CollectorRegistry(), tracer_provider.get_tracer(SERVICE_NAME)
    authentication = auth_service(settings, auth, clock)
    gate = session_gate(settings, authentication, auth, repositories, clock)
    streams = StreamRegistry()
    syncs = sync_factory(settings, merkle, clock)
    stream = session_stream(
        gate, streams, syncs=syncs, clock=clock, metrics=registry, tracer=tracer
    )
    closers = (database.dispose, redis.aclose, keystore.aclose)
    app = FastAPI(
        title="XYPE backend",
        lifespan=_lifespan(settings, tracer_provider, closers, stream, streams, revocations),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.metrics_registry = registry
    app.state.tracer_provider = tracer_provider
    app.state.health_service = HealthService(
        probes, timeout_seconds=settings.readiness_timeout_seconds
    )
    app.state.auth_service = authentication
    app.state.session_key_service = session_key_service(settings, auth, clock)
    app.state.session_stream = stream
    _mount(app, tracer, HttpMetrics(registry))
    return app


def _lifespan(
    settings: BackendSettings,
    tracer_provider: TracerProvider,
    closers: Sequence[Callable[[], Awaitable[object]]],
    stream: SessionStream,
    streams: StreamRegistry,
    revocations: RevocationSubscriber,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Starts the Session stream server and the revocation listener. On shutdown, in
    order: every stream is told to reconnect elsewhere (drain), the server gives them the
    grace period, the listener stops, the stores close, the last spans are flushed."""

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
            server, app.state.grpc_port = create_grpc_server(settings, stream)
            await server.start()
            stack.push_async_callback(server.stop, settings.stream_shutdown_grace_s)
            stack.callback(streams.drain)
            yield
        logger.info("backend.stopped")

    return lifespan


def _report_early_end(task: asyncio.Task[None]) -> None:
    """The listener only ends by cancellation; anything else means streams now close at
    their next message only, so it is logged (the type only) as soon as it happens."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("session.revocations.listener_failed", error=type(task.exception()).__name__)


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
