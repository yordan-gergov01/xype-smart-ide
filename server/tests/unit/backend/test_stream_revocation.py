"""Revocation reaches open `Session` streams (ADR-0013, ADR-0014, ADR-0015): through the
revocation channel at once, and through the per-message session-key check even when the
message is lost. Shutdown drains every stream with a reconnect notice."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from grpc import aio
from redis.exceptions import ConnectionError as RedisConnectionError
from support.auth import FakeRevocationPublisher
from support.backend import FakeAuth, make_settings
from support.streams import Call, StreamWorld, heartbeat, hello, stream_server
from xype.v1 import session_pb2

from backend.core.auth import auth_service, client_admin
from backend.dal.auth import RedisRevocationSubscriber
from backend.main import create_app
from backend.services.session import RefusedError, StreamRegistry, listen_for_revocations
from common_lib.auth import (
    AccessClaims,
    Credential,
    InvalidTokenError,
    NewClient,
    Principal,
    Revocation,
)
from common_lib.secret_bytes import SecretBytes

Code = session_pb2.StreamError
KEY_UNUSABLE = "session key unknown, expired or not yours"


async def _open(world: StreamWorld, client: NewClient) -> tuple[str, str, AccessClaims]:
    """A new window of `client`: its token, a session key and its claims."""
    token, claims = await world.login(client)
    return token, await world.session_key(claims), claims


async def _read(call: Call) -> session_pb2.SessionResponse:
    out = await asyncio.wait_for(call.read(), timeout=5)
    assert isinstance(out, session_pb2.SessionResponse)
    return out


async def _until_empty(streams: StreamRegistry) -> None:
    for _ in range(100):
        if not len(streams):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("streams left in the registry")


def _claims(user: uuid.UUID, family: uuid.UUID) -> AccessClaims:
    now = datetime.now(UTC)
    principal = Principal(user, uuid.uuid4(), "developer")
    return AccessClaims(principal, family, now + timedelta(minutes=15), now)


async def test_disabling_the_client_closes_its_open_stream_at_once() -> None:
    async with stream_server() as world:
        client = await world.client()
        token, key_id, claims = await _open(world, client)
        repository = world.repositories.register(claims.principal.team_id)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            admin = client_admin(world.settings, world.auth.deps, world.clock)
            await admin.disable_client(client.client_id)
            closed = await _read(call)
            assert await call.read() is aio.EOF
        await _until_empty(world.streams)
    assert (closed.error.code, closed.error.message) == (
        Code.CODE_UNAUTHENTICATED,
        "access revoked",
    )


async def test_refresh_token_reuse_closes_that_window_only() -> None:
    async with stream_server() as world:
        client = await world.client()
        service = auth_service(world.settings, world.auth.deps, world.clock)
        secret = SecretBytes(client.client_secret.encode())
        stolen = await service.login(Credential("client_credentials", client.client_id, secret))
        key_a = await world.session_key(await service.authenticate(stolen.access_token))
        token_b, key_b, _ = await _open(world, client)
        async with world.stream(stolen.access_token) as a, world.stream(token_b) as b:
            await a.write(hello(key_a))
            await b.write(hello(key_b))
            await _read(a)
            await _read(b)
            await service.refresh(stolen.refresh_token)
            with pytest.raises(InvalidTokenError):
                await service.refresh(stolen.refresh_token)  # reuse: family A is revoked
            closed = await _read(a)
            await b.write(heartbeat(1))
            beat = await _read(b)
    assert closed.error.message == "access revoked"
    assert beat.heartbeat.seq == 1


async def test_the_key_check_closes_a_revoked_stream_without_the_message() -> None:
    """The backstop: pub/sub is at most once, so a lost message still ends the stream at
    the next message, because revocation deletes the session key before it publishes."""
    async with stream_server() as world:
        token, key_id, claims = await _open(world, await world.client())
        async with world.stream(token) as call:
            await call.write(hello(key_id))
            await _read(call)
            # Deleted as a revocation would, but nothing is published.
            await world.auth.keys.revoke_family(str(claims.family_id))
            await call.write(heartbeat(1))
            refused = await _read(call)
            assert await call.read() is aio.EOF
    assert refused.error.message == KEY_UNUSABLE


async def test_a_revoked_users_token_opens_a_stream_but_its_key_is_gone() -> None:
    async with stream_server() as world:
        client = await world.client()
        token, key_id, _ = await _open(world, client)
        admin = client_admin(world.settings, world.auth.deps, world.clock)
        await admin.disable_client(client.client_id)
        # The access token still verifies until it expires; Hello finds no key.
        async with world.stream(token) as call:
            await call.write(hello(key_id))
            refused = await _read(call)
    assert refused.error.message == KEY_UNUSABLE


async def test_drain_tells_every_stream_to_reconnect() -> None:
    async with stream_server() as world:
        token, key_id, _ = await _open(world, await world.client())
        async with world.stream(token) as call:
            await call.write(hello(key_id))
            await _read(call)
            assert world.streams.drain() == 1
            notice = await _read(call)
            assert await call.read() is aio.EOF
        # A stream opened while draining is told the same at once.
        token, _, _ = await _open(world, await world.client("Late"))
        async with world.stream(token) as late:
            assert (await _read(late)).error.code == Code.CODE_UNAVAILABLE
    assert (notice.error.code, notice.error.message) == (
        Code.CODE_UNAVAILABLE,
        "server restarting",
    )


async def test_shutting_the_app_down_drains_its_streams() -> None:
    settings = make_settings()
    fakes = FakeAuth(settings)
    app = create_app(
        settings, probes=[], auth=fakes.deps, revocations=fakes.revocations.subscriber()
    )
    world = StreamWorld(settings, fakes.clock, fakes)
    token, key_id, _ = await _open(world, await world.client())
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    world.port = app.state.grpc_port
    async with world.stream(token) as call:
        await call.write(hello(key_id))
        await _read(call)
        notice = asyncio.create_task(_read(call))
        await lifespan.__aexit__(None, None, None)
        assert (await notice).error.message == "server restarting"


def test_the_registry_ends_the_streams_a_revocation_names() -> None:
    streams = StreamRegistry()
    user, other = uuid.uuid4(), uuid.uuid4()
    window_a, window_b, stranger = (
        streams.open(_claims(user, uuid.uuid4())),
        streams.open(_claims(user, uuid.uuid4())),
        streams.open(_claims(other, uuid.uuid4())),
    )
    assert streams.revoke(Revocation("family", window_a.family_id)) == 1
    assert (window_a.reason, window_b.reason) == ("revoked", None)
    assert streams.revoke(Revocation("user", user)) == 2
    assert window_b.reason == "revoked"
    assert stranger.reason is None
    for ticket in (window_a, window_b, stranger):
        streams.close(ticket)
    assert len(streams) == 0
    assert streams.revoke(Revocation("user", user)) == 0


async def test_the_listener_feeds_the_registry() -> None:
    publisher = FakeRevocationPublisher()
    streams = StreamRegistry()
    ticket = streams.open(_claims(uuid.uuid4(), uuid.uuid4()))
    listener = asyncio.create_task(listen_for_revocations(publisher.subscriber(), streams))
    await publisher.publish(Revocation("user", ticket.user_id))
    await asyncio.wait_for(ticket.ended.wait(), timeout=2)
    listener.cancel()
    await asyncio.gather(listener, return_exceptions=True)
    assert ticket.reason == "revoked"


class _DownPubSub:
    async def subscribe(self, *_: str) -> None:
        raise RedisConnectionError("connection refused")

    async def aclose(self) -> None:
        return None


class _DownRedis:
    """A Redis that refuses every subscription at once."""

    def pubsub(self, **_: object) -> _DownPubSub:
        return _DownPubSub()


async def test_the_subscriber_keeps_retrying_while_redis_is_down() -> None:
    subscriber = RedisRevocationSubscriber(_DownRedis(), first_backoff_s=0.01)  # type: ignore[arg-type]  # reason: a stub with the two calls used
    revocations = subscriber.revocations()
    with structlog.testing.capture_logs() as logs:
        waiting = asyncio.ensure_future(anext(revocations))
        await asyncio.sleep(0.2)
        assert not waiting.done()
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    await revocations.aclose()
    retries = [e for e in logs if e["event"] == "auth.revocations.disconnected"]
    assert len(retries) >= 3
    # The type only: the exception's message never reaches the log (rule 08).
    assert {e["error"] for e in retries} == {"ConnectionError"}


class _ScriptedPubSub:
    """Delivers the given channel messages, then nothing."""

    def __init__(self, data: list[object]) -> None:
        self._data = data

    async def subscribe(self, *_: str) -> None:
        return None

    async def get_message(self, timeout: float) -> dict[str, object] | None:  # noqa: ASYNC109 - redis-py's signature
        if self._data:
            return {"type": "message", "data": self._data.pop(0)}
        await asyncio.sleep(timeout)
        return None

    async def aclose(self) -> None:
        return None


class _ScriptedRedis:
    def __init__(self, data: list[object]) -> None:
        self._pubsub = _ScriptedPubSub(data)

    def pubsub(self, **_: object) -> _ScriptedPubSub:
        return self._pubsub


async def test_the_subscriber_skips_anything_but_a_revocation() -> None:
    valid = Revocation("family", uuid.uuid4())
    junk: list[object] = [
        "not json",
        b"\xff\xfe",
        "[1, 2]",
        '{"kind": "team", "id": "x"}',
        '{"kind": "user"}',
        '{"kind": "user", "id": 5}',
        '{"kind": "user", "id": ["x"]}',
        '{"kind": "user", "id": "not-a-uuid"}',
        f'{{"kind": "team", "id": "{uuid.uuid4()}"}}',
        "[" * 100_000,
        42,
    ]
    redis = _ScriptedRedis([*junk, f'{{"kind": "family", "id": "{valid.subject_id}"}}'])
    revocations = RedisRevocationSubscriber(redis).revocations()  # type: ignore[arg-type]  # reason: a stub with the calls used
    with structlog.testing.capture_logs() as logs:
        assert await asyncio.wait_for(anext(revocations), timeout=2) == valid
    await revocations.aclose()
    malformed = [e for e in logs if e["event"] == "auth.revocations.malformed"]
    assert len(malformed) == len(junk)
    assert all(set(e) == {"event", "log_level"} for e in malformed)


async def test_a_revocation_during_a_message_reports_the_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key vanishes with the revocation while a message is handled: the client is told
    "access revoked", not why that message failed."""
    async with stream_server() as world:
        token, key_id, claims = await _open(world, await world.client())

        async def revoked_meanwhile(*_: object) -> None:
            world.streams.revoke(Revocation("family", claims.family_id))
            raise RefusedError(KEY_UNUSABLE)

        async with world.stream(token) as call:
            await call.write(hello(key_id))
            await _read(call)
            monkeypatch.setattr(world.gate, "message", revoked_meanwhile)
            await call.write(heartbeat(1))
            closed = await _read(call)
            assert await call.read() is aio.EOF
    assert (closed.error.code, closed.error.message) == (
        Code.CODE_UNAUTHENTICATED,
        "access revoked",
    )


class _BrokenSubscriber:
    async def revocations(self) -> AsyncIterator[Revocation]:
        nothing: tuple[Revocation, ...] = ()
        for revocation in nothing:  # an async generator that fails before its first item
            yield revocation
        raise RuntimeError("secret-detail-456")


async def test_a_listener_that_dies_is_reported_at_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = make_settings()
    fakes = FakeAuth(settings)
    app = create_app(settings, probes=[], auth=fakes.deps, revocations=_BrokenSubscriber())
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        out = capsys.readouterr().out
    assert "session.revocations.listener_failed" in out
    assert "RuntimeError" in out
    assert "secret-detail-456" not in out
