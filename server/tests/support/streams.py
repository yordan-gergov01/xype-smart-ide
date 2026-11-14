"""A `Session` stream server on loopback with TLS and the fakes, and a client for it.

grpcio opens its sockets in native code, outside the network guard, so the server binds
127.0.0.1 only and the client dials it there (ADR-0012).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import grpc
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from grpc import aio
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from xype.v1 import session_pb2

from backend.core.auth import auth_service, client_admin, session_key_service
from backend.core.session import session_gate, sync_factory
from backend.core.settings import BackendSettings
from backend.grpc import METHOD, SERVICE, SessionStream, StreamMetrics, create_grpc_server
from backend.services.session import SessionGate, StreamRegistry, listen_for_revocations
from common_lib.auth import AccessClaims, Credential, NewClient
from common_lib.clock import Clock
from common_lib.secret_bytes import SecretBytes
from support.backend import FakeAuth, make_settings
from support.merkle import FakeMerkleIndex
from support.session import FakeRepositoryAccess
from support.tls import ca_file

type Call = aio.StreamStreamCall[session_pb2.SessionRequest, session_pb2.SessionResponse]


class ShiftedClock:
    """The wall clock moved by `offset`."""

    def __init__(self, offset: timedelta = timedelta()) -> None:
        self.offset = offset

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset


@dataclass
class StreamWorld:
    settings: BackendSettings
    clock: Clock
    auth: FakeAuth
    repositories: FakeRepositoryAccess = field(default_factory=FakeRepositoryAccess)
    merkle: FakeMerkleIndex = field(default_factory=FakeMerkleIndex)
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    port: int = 0
    gate: SessionGate | None = None
    streams: StreamRegistry = field(default_factory=StreamRegistry)

    async def client(self, team: str = "Web") -> NewClient:
        admin = client_admin(self.settings, self.auth.deps, self.clock)
        if await self.auth.users.find_team(team) is None:
            await admin.create_team(team)
        return await admin.create_client(team=team, email=f"{team}@x.test", display_name="D")

    async def login(
        self, client: NewClient, *, issued_by: Clock | None = None
    ) -> tuple[str, AccessClaims]:
        """`issued_by` signs the token on another clock: one shifted into the past gives a
        token that expires within seconds on the server's clock."""
        service = auth_service(self.settings, self.auth.deps, issued_by or self.clock)
        secret = SecretBytes(client.client_secret.encode())
        pair = await service.login(Credential("client_credentials", client.client_id, secret))
        return pair.access_token, await service.authenticate(pair.access_token)

    async def session_key(self, claims: AccessClaims) -> str:
        public = (
            X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        minted = await session_key_service(self.settings, self.auth.deps, self.clock).mint(
            claims, public
        )
        return minted.key_id

    def team_of(self, claims: AccessClaims) -> uuid.UUID:
        return claims.principal.team_id

    def repository(self, team_id: uuid.UUID) -> uuid.UUID:
        """A repository registered for the team, never synced."""
        repository_id = self.repositories.register(team_id)
        self.merkle.register(repository_id)
        return repository_id

    @asynccontextmanager
    async def stream(self, token: str | None) -> AsyncIterator[Call]:
        credentials = grpc.ssl_channel_credentials(root_certificates=ca_file().read_bytes())
        async with aio.secure_channel(f"127.0.0.1:{self.port}", credentials) as channel:
            method: aio.StreamStreamMultiCallable[
                session_pb2.SessionRequest, session_pb2.SessionResponse
            ] = channel.stream_stream(
                f"/{SERVICE}/{METHOD}",
                request_serializer=session_pb2.SessionRequest.SerializeToString,
                response_deserializer=session_pb2.SessionResponse.FromString,
            )
            metadata = (("authorization", f"Bearer {token}"),) if token else ()
            call = method(metadata=metadata)
            try:
                yield call
            finally:
                call.cancel()


@asynccontextmanager
async def stream_server(
    clock: Clock | None = None, **overrides: object
) -> AsyncIterator[StreamWorld]:
    """A started server on loopback; `overrides` change the settings."""
    settings = make_settings(**overrides)
    clock = clock or ShiftedClock()
    world = StreamWorld(settings, clock, FakeAuth(settings, clock=clock))
    world.gate = gate = session_gate(
        settings,
        auth_service(settings, world.auth.deps, clock),
        world.auth.deps,
        world.repositories,
        clock,
    )
    tracer = TracerProvider().get_tracer("test")
    metrics = StreamMetrics(world.registry)
    syncs = sync_factory(settings, world.merkle, clock)
    handler = SessionStream(
        gate, world.streams, syncs=syncs, clock=clock, metrics=metrics, tracer=tracer
    )
    server, world.port = create_grpc_server(settings, handler)
    # Revocations published by the fakes reach this server's streams, as over Redis.
    subscriber = world.auth.revocations.subscriber()
    listener = asyncio.create_task(listen_for_revocations(subscriber, world.streams))
    await server.start()
    try:
        yield world
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        await server.stop(0)


def hello(key_id: str, *repositories: uuid.UUID, **roots: str) -> session_pb2.SessionRequest:
    return session_pb2.SessionRequest(
        hello=session_pb2.Hello(
            session_key_id=key_id,
            repository_ids=[str(r) for r in repositories],
            repository_roots=roots,
        )
    )


def heartbeat(seq: int) -> session_pb2.SessionRequest:
    return session_pb2.SessionRequest(heartbeat=session_pb2.Heartbeat(seq=seq))
