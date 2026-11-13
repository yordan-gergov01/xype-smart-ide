"""The `Session` stream over gRPC with TLS on loopback (ADR-0013): authentication on open,
Hello/HelloAck, heartbeats, refusals with fixed `StreamError`s, the token-expiry notice
and close, and that no token reaches a log line."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import grpc
import pytest
from grpc import aio
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from support.backend import FakeAuth, make_settings
from support.streams import Call, ShiftedClock, StreamWorld, heartbeat, hello, stream_server
from support.tls import ca_file
from xype.v1 import common_pb2, feedback_pb2, index_pb2, session_pb2

from backend.grpc import SessionStream, StreamMetrics
from backend.main import create_app
from common_lib.auth import RateLimitedError

Code = session_pb2.StreamError


async def _ready(
    world: StreamWorld, team: str = "Web", *, issued_by: ShiftedClock | None = None
) -> tuple[str, str, uuid.UUID]:
    """A token, a session key and one repository of the team."""
    token, claims = await world.login(await world.client(team), issued_by=issued_by)
    repository = world.repositories.register(world.team_of(claims))
    return token, await world.session_key(claims), repository


async def _read(call: Call) -> session_pb2.SessionResponse:
    out = await call.read()
    assert out is not aio.EOF
    assert isinstance(out, session_pb2.SessionResponse)
    return out


@pytest.mark.parametrize("token", [None, "not-a-token"])
async def test_refuses_a_stream_without_a_valid_token(token: str | None) -> None:
    async with stream_server() as world, world.stream(token) as call:
        await call.write(heartbeat(1))
        with pytest.raises(aio.AioRpcError) as refused:
            await call.read()
    assert refused.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert refused.value.details() == "invalid_token"


async def test_hello_heartbeat_and_correlation(capsys: pytest.CaptureFixture[str]) -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)
        async with world.stream(token) as call:
            request = hello(key_id, repository)
            request.envelope.CopyFrom(common_pb2.Envelope(message_id="m-1"))
            await call.write(request)
            ack = await _read(call)
            await call.write(heartbeat(7))
            beat = await _read(call)
    assert ack.WhichOneof("body") == "hello_ack"
    assert ack.hello_ack.heartbeat_interval_s == 20
    assert ack.hello_ack.rate_limit_per_minute == 200
    assert ack.envelope.correlation_id == "m-1"
    assert ack.envelope.message_id
    assert ack.envelope.sent_at.seconds > 0
    assert beat.heartbeat.seq == 7
    assert beat.envelope.correlation_id == ack.hello_ack.session_id
    assert token not in capsys.readouterr().out


async def test_hello_comes_first_and_ends_the_stream_otherwise() -> None:
    async with stream_server() as world:
        token, _, _ = await _ready(world)
        async with world.stream(token) as call:
            await call.write(heartbeat(1))
            refused = await _read(call)
            assert await call.read() is aio.EOF
    assert (refused.error.code, refused.error.message) == (
        Code.CODE_INVALID_REQUEST,
        "hello expected first",
    )


async def test_another_teams_repository_ends_the_stream() -> None:
    async with stream_server() as world:
        token, key_id, own = await _ready(world, "Mine")
        _, _, foreign = await _ready(world, "Theirs")
        async with world.stream(token) as call:
            await call.write(hello(key_id, own, foreign))
            refused = await _read(call)
            assert await call.read() is aio.EOF
    assert refused.error.message == "repository not accessible"
    assert str(foreign) not in refused.SerializeToString().decode("latin-1")


async def test_unsupported_messages_are_refused_and_the_stream_goes_on() -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            feedback = session_pb2.SessionRequest(feedback=feedback_pb2.Feedback())
            await call.write(feedback)
            refused = await _read(call)
            await call.write(heartbeat(2))
            beat = await _read(call)
    assert refused.error.message == "message not supported yet"
    assert beat.heartbeat.seq == 2


async def test_a_stream_without_hello_closes_after_the_timeout() -> None:
    async with stream_server(stream_hello_timeout_s=1) as world:
        token, _, _ = await _ready(world)
        async with world.stream(token) as call:
            closed = await _read(call)
            assert await call.read() is aio.EOF
    assert (closed.error.code, closed.error.message) == (
        Code.CODE_INVALID_REQUEST,
        "hello expected first",
    )


async def test_token_expiry_sends_the_notice_then_closes() -> None:
    async with stream_server() as world:
        # Issued a token lifetime minus 2 s ago: it expires 2 s from now.
        past = ShiftedClock(-timedelta(seconds=world.settings.auth_access_ttl_s - 2))
        token, key_id, repository = await _ready(world, issued_by=past)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            notice = await _read(call)
            ack = await _read(call)
            closed = await _read(call)
            assert await call.read() is aio.EOF
    assert notice.WhichOneof("body") == "token_refresh_required"
    assert notice.token_refresh_required.expires_at.seconds > 0
    assert ack.WhichOneof("body") == "hello_ack"
    assert (closed.error.code, closed.error.message) == (
        Code.CODE_UNAUTHENTICATED,
        "access token expired",
    )
    closes = world.registry.get_sample_value(
        "xype_grpc_streams_closed_total", {"reason": "expired"}
    )
    assert closes == 1


async def test_messages_past_the_rate_limit_are_refused_with_retry_after() -> None:
    async with stream_server(rate_limit_per_minute=3) as world:
        token, key_id, repository = await _ready(world)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))  # open (1), hello (2)
            await _read(call)
            await call.write(heartbeat(1))  # 3
            await _read(call)
            await call.write(heartbeat(2))
            limited = await _read(call)
    assert limited.error.code == Code.CODE_RATE_LIMITED
    assert limited.error.retry_after_ms > 0


async def test_opening_fails_closed_without_the_limiter_store() -> None:
    async with stream_server() as world:
        token, _, _ = await _ready(world)
        world.auth.limiter.down = True
        async with world.stream(token) as call:
            await call.write(heartbeat(1))
            with pytest.raises(aio.AioRpcError) as refused:
                await call.read()
    assert refused.value.code() is grpc.StatusCode.UNAVAILABLE


async def test_the_app_lifespan_serves_the_stream() -> None:
    settings = make_settings()
    fakes = FakeAuth(settings)
    revocations = fakes.revocations.subscriber()
    app = create_app(settings, probes=[], auth=fakes.deps, revocations=revocations)
    async with app.router.lifespan_context(app):
        credentials = grpc.ssl_channel_credentials(root_certificates=ca_file().read_bytes())
        target = f"127.0.0.1:{app.state.grpc_port}"
        async with aio.secure_channel(target, credentials) as channel:
            method = channel.stream_stream(
                "/xype.v1.SessionService/Session",
                request_serializer=session_pb2.SessionRequest.SerializeToString,
                response_deserializer=session_pb2.SessionResponse.FromString,
            )
            call = method()
            await call.write(heartbeat(1))
            with pytest.raises(aio.AioRpcError) as refused:
                await call.read()
    assert refused.value.code() is grpc.StatusCode.UNAUTHENTICATED


async def test_a_client_that_keeps_ignoring_the_rate_limit_is_cut_off() -> None:
    async with stream_server(rate_limit_per_minute=3) as world:
        token, key_id, repository = await _ready(world)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            codes = []
            for seq in range(20):
                try:
                    await call.write(heartbeat(seq))
                except aio.AioRpcError:
                    break  # the server ended the stream before this write
                out = await call.read()
                if out is aio.EOF:
                    break
                assert isinstance(out, session_pb2.SessionResponse)
                codes.append(out.error.code)
    limited = [c for c in codes if c == Code.CODE_RATE_LIMITED]
    assert limited == [Code.CODE_RATE_LIMITED] * 5
    assert codes[-5:] == limited
    closes = world.registry.get_sample_value(
        "xype_grpc_streams_closed_total", {"reason": "rate_limited"}
    )
    assert closes == 1


async def test_refusals_after_hello_keep_the_stream_and_a_half_close_ends_it() -> None:
    async with stream_server() as world:
        token, key_id, own = await _ready(world, "Mine")
        _, _, foreign = await _ready(world, "Theirs")
        async with world.stream(token) as call:
            await call.write(hello(key_id, own))
            await _read(call)
            await call.write(hello(key_id, own))
            twice = await _read(call)
            report = index_pb2.MerkleTreeReport(repository_id=str(foreign))
            await call.write(session_pb2.SessionRequest(merkle_tree_report=report))
            foreign_report = await _read(call)
            await call.write(heartbeat(3))
            beat = await _read(call)
            await call.done_writing()
            assert await call.read() is aio.EOF
    assert twice.error.message == "hello already received"
    assert foreign_report.error.message == "repository not accessible"
    assert beat.heartbeat.seq == 3
    closes = world.registry.get_sample_value(
        "xype_grpc_streams_closed_total", {"reason": "client_closed"}
    )
    assert closes == 1


async def test_a_refused_hello_does_not_stop_the_hello_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with stream_server(stream_hello_timeout_s=1) as world:
        token, key_id, repository = await _ready(world)

        async def limited(*_: object) -> None:
            raise RateLimitedError(1.0)

        monkeypatch.setattr(world.gate, "hello", limited)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            refused = await _read(call)
            closed = await _read(call)
            assert await call.read() is aio.EOF
    assert refused.error.code == Code.CODE_RATE_LIMITED
    assert closed.error.message == "hello expected first"


async def test_an_unexpected_error_ends_the_stream_with_a_fixed_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)

        async def broken(*_: object) -> None:
            raise RuntimeError("secret-detail-123")

        monkeypatch.setattr(world.gate, "hello", broken)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            failed = await _read(call)
            assert await call.read() is aio.EOF
    assert (failed.error.code, failed.error.message) == (Code.CODE_INTERNAL, "internal error")
    assert "secret-detail-123" not in capsys.readouterr().out
    opens = world.registry.get_sample_value("xype_grpc_stream_opens_total", {"outcome": "ok"})
    assert opens == 1


async def test_a_second_hello_counts_against_the_rate_limit() -> None:
    async with stream_server(rate_limit_per_minute=4) as world:
        token, key_id, repository = await _ready(world)
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            codes = []
            for _ in range(3):
                await call.write(hello(key_id, repository))
                codes.append((await _read(call)).error.code)
    assert Code.CODE_RATE_LIMITED in codes


class _AbortedError(Exception):
    pass


class _Context:
    """The two calls the handler makes on a gRPC servicer context."""

    def __init__(self, token: str) -> None:
        self.token = token

    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return (("authorization", f"Bearer {self.token}"),)

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise _AbortedError(code, details)


async def test_a_failed_read_ends_the_stream_and_logs_only_the_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)

        async def requests() -> AsyncIterator[session_pb2.SessionRequest]:
            yield hello(key_id, repository)
            raise RuntimeError("payload-xyz")

        stream = SessionStream(
            world.gate,  # type: ignore[arg-type]  # reason: set by stream_server
            world.streams,
            clock=world.clock,
            metrics=StreamMetrics(CollectorRegistry()),
            tracer=TracerProvider().get_tracer("test"),
        )
        context = _Context(token)
        out = [r async for r in stream.handle(requests(), context)]  # type: ignore[arg-type]  # reason: a duck-typed context
    assert [r.WhichOneof("body") for r in out] == ["hello_ack"]
    logs = capsys.readouterr().out
    assert "payload-xyz" not in logs
    assert "session.read_failed" in logs
    assert "error=RuntimeError" in logs
    assert "reason=read_failed" in logs


def _closes(world: StreamWorld) -> dict[str, float]:
    return {
        sample.labels["reason"]: sample.value
        for metric in world.registry.collect()
        if metric.name == "xype_grpc_streams_closed"
        for sample in metric.samples
        if sample.name.endswith("_total")
    }


async def test_a_cancelled_stream_leaves_no_task_behind() -> None:
    async with stream_server() as world:
        token, key_id, repository = await _ready(world)
        before = len(asyncio.all_tasks())
        async with world.stream(token) as call:
            await call.write(hello(key_id, repository))
            await _read(call)
            call.cancel()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if _closes(world) and len(asyncio.all_tasks()) <= before:
                break
        assert len(asyncio.all_tasks()) <= before
    # The server sees the cancel either as such or, first, as the client's side ending.
    (reason, count), *more = _closes(world).items()
    assert (count, more) == (1, [])
    assert reason in {"cancelled", "client_closed"}


async def test_a_root_report_for_another_teams_repository_is_refused() -> None:
    """`MerkleRootReport` (slice 7b) names a repository: it is checked like every other
    message before it is refused as not handled yet (slice 7c)."""
    async with stream_server() as world:
        token, key_id, own = await _ready(world, "Mine")
        _, _, foreign = await _ready(world, "Theirs")
        async with world.stream(token) as call:
            await call.write(hello(key_id, own))
            await _read(call)
            answers = []
            for repository in (foreign, own):
                report = index_pb2.MerkleRootReport(repository_id=str(repository))
                await call.write(session_pb2.SessionRequest(merkle_root_report=report))
                answers.append(await _read(call))
    assert answers[0].error.message == "repository not accessible"
    assert str(foreign) not in answers[0].SerializeToString().decode("latin-1")
    assert answers[1].error.message != "repository not accessible"
