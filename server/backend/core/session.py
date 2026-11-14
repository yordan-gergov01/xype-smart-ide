"""Builds the `Session` stream (ADR-0013) and its Merkle syncs (ADR-0018) from the
settings; used by `backend.main`."""

import uuid

from opentelemetry.trace import Tracer
from prometheus_client import CollectorRegistry

from backend.core.settings import BackendSettings
from backend.grpc import SessionStream, StreamMetrics, SyncFactory
from backend.services.auth import AuthDependencies, AuthService
from backend.services.indexing import MerkleIndex, MerkleSyncs, NodeBudget, SyncPolicy
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
        merkle_poll_interval_s=settings.merkle_poll_interval_s,
    )


def sync_factory(settings: BackendSettings, index: MerkleIndex, clock: Clock) -> SyncFactory:
    """Each stream gets its own syncs over the repositories authorized at its Hello."""
    policy = SyncPolicy(
        deadline_s=settings.merkle_sync_deadline_s,
        max_restarts=settings.merkle_sync_max_restarts,
    )
    budget = NodeBudget(settings.merkle_sync_max_held_nodes)  # shared by every stream

    def build(repositories: frozenset[uuid.UUID]) -> MerkleSyncs:
        return MerkleSyncs(index, repositories, policy=policy, budget=budget, clock=clock)

    return build


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
    syncs: SyncFactory,
    clock: Clock,
    metrics: CollectorRegistry,
    tracer: Tracer,
) -> SessionStream:
    return SessionStream(
        gate, streams, syncs=syncs, clock=clock, metrics=StreamMetrics(metrics), tracer=tracer
    )
