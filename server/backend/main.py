"""Composition root of the backend: `uvicorn backend.main:create_app --factory`.

The only place where adapters are built and wired (ADR-0009). Nothing connects at
startup; readiness reports which dependency is missing.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import CollectorRegistry

from backend.api.health import router as health_router
from backend.api.middleware import HttpMetrics, RequestContextMiddleware, UnhandledErrorMiddleware
from backend.core.connections import create_database_engine, create_redis_client
from backend.core.settings import BackendSettings
from backend.dal.health import DatabaseProbe, RedisProbe
from backend.messaging.health import BrokerProbe
from backend.services.health import DependencyProbe, HealthService
from common_lib.logging_config import configure_logging
from common_lib.settings import load_settings
from common_lib.tracing import configure_tracing

SERVICE_NAME = "backend"

logger = structlog.get_logger(__name__)


def create_app(
    settings: BackendSettings | None = None,
    *,
    probes: Sequence[DependencyProbe] | None = None,
) -> FastAPI:
    """Build the app. Settings come from the environment unless given; tests pass fakes."""
    settings = settings or load_settings(BackendSettings)
    configure_logging(
        service=SERVICE_NAME, level=settings.log_level, log_format=settings.log_format
    )
    tracer_provider = configure_tracing(
        service=SERVICE_NAME, otlp_endpoint=settings.otel_exporter_otlp_endpoint
    )
    engine = create_database_engine(settings)
    redis = create_redis_client(settings)
    if probes is None:
        probes = (DatabaseProbe(engine), RedisProbe(redis), BrokerProbe(settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("backend.started")
        # Each step runs even if an earlier one fails (exit stack: last pushed, first run).
        async with AsyncExitStack() as stack:
            # The final span flush is a blocking network call: off the event loop.
            stack.push_async_callback(asyncio.to_thread, tracer_provider.shutdown)
            stack.push_async_callback(engine.dispose)
            stack.push_async_callback(redis.aclose)
            yield
        logger.info("backend.stopped")

    app = FastAPI(
        title="XYPE backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    registry = CollectorRegistry()
    app.state.metrics_registry = registry
    app.state.tracer_provider = tracer_provider
    app.state.health_service = HealthService(
        probes, timeout_seconds=settings.readiness_timeout_seconds
    )
    app.include_router(health_router)

    # Starlette runs the last added middleware first, so this list is innermost first.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        RequestContextMiddleware,
        tracer=tracer_provider.get_tracer(SERVICE_NAME),
        metrics=HttpMetrics(registry),
    )
    return app
