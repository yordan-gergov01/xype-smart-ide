"""Builds the `Session` stream (ADR-0013) from the settings; used by `backend.main`."""

from opentelemetry.trace import Tracer
from prometheus_client import CollectorRegistry

from backend.core.settings import BackendSettings
from backend.grpc import SessionStream, StreamMetrics
from backend.services.auth import AuthDependencies, AuthService
from backend.services.session import (
    RepositoryAccess,
    SessionGate,
    StreamPolicy,
    StreamRegistry,
)
from common_lib.clock import Clock


def stream_policy(settings: BackendSettings) -> StreamPolicy:
    return StreamPolicy(
        heartbeat_interval_s=settings.stream_heartbeat_interval_s,
        idle_timeout_s=settings.stream_idle_timeout_s,
        hello_timeout_s=settings.stream_hello_timeout_s,
        refresh_lead_s=settings.stream_refresh_lead_s,
        max_repositories=settings.stream_max_repositories,
        requests_per_minute=settings.rate_limit_per_minute,
    )


def session_gate(
    settings: BackendSettings,
    auth: AuthService,
    deps: AuthDependencies,
    repositories: RepositoryAccess,
    clock: Clock,
) -> SessionGate:
    return SessionGate(auth, deps.keys, repositories, policy=stream_policy(settings), clock=clock)


def session_stream(
    gate: SessionGate,
    streams: StreamRegistry,
    *,
    clock: Clock,
    metrics: CollectorRegistry,
    tracer: Tracer,
) -> SessionStream:
    return SessionStream(gate, streams, clock=clock, metrics=StreamMetrics(metrics), tracer=tracer)
