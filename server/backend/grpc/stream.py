"""The handler of `/xype.v1.SessionService/Session` (ADR-0013): one bidirectional stream
per IDE window.

The call is authenticated when it opens (bearer token in the `authorization` metadata).
After that every message is checked by `SessionGate`, and the stream's own timers
(`StreamTimers`) send `TokenRefreshRequired` and close it at token expiry, without a
Hello, or after silence. A refusal answers with a `StreamError` carrying a fixed string;
nothing from a request is echoed, logged or put on a span (rule 08).
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import NoReturn

import grpc
import structlog
from grpc import aio
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer
from xype.v1 import session_pb2

from backend.grpc import messages
from backend.grpc.metrics import StreamMetrics
from backend.services.session import (
    HELLO_FIRST,
    HELLO_TWICE,
    KEY_UNUSABLE,
    UNSUPPORTED,
    Due,
    OpenSession,
    RefusedError,
    SessionGate,
    StreamTimers,
)
from common_lib.auth import AccessClaims, InvalidTokenError, RateLimitedError
from common_lib.clock import Clock
from common_lib.correlation import CORRELATION_ID_HEADER, is_valid_correlation_id
from common_lib.domain import uuid7
from common_lib.errors import DependencyUnavailableError

logger = structlog.get_logger(__name__)

type Request = session_pb2.SessionRequest
type Response = session_pb2.SessionResponse
type Context = aio.ServicerContext[Request, Response]

Code = session_pb2.StreamError
_CLOSES = {
    Due.EXPIRED: (Code.CODE_UNAUTHENTICATED, "access token expired"),
    Due.NO_HELLO: (Code.CODE_INVALID_REQUEST, HELLO_FIRST),
    Due.IDLE: (Code.CODE_TIMEOUT, "no heartbeat"),
}
_BEARER = "bearer "
# Messages read ahead (each up to the receive cap): the handler is sequential, so beyond a
# few, HTTP/2 flow control holds the client back instead of the backend's memory.
INBOX = 2
# A client that keeps sending past its rate limit is cut off instead of costing a
# limiter call per message.
_RATE_LIMITED_IN_A_ROW = 5


class _Wait(Enum):
    TIMEOUT = "timeout"  # a timer may be due
    ENDED = "ended"  # the client's side ended and every message was handled


@dataclass
class _Stream:
    session_id: str
    claims: AccessClaims
    timers: StreamTimers
    open: OpenSession | None = None
    reason: str | None = None  # why it closes; set once
    messages: int = field(default=0)
    rate_limited: int = field(default=0)  # in a row


class SessionStream:
    def __init__(
        self, gate: SessionGate, *, clock: Clock, metrics: StreamMetrics, tracer: Tracer
    ) -> None:
        self._gate = gate
        self._clock = clock
        self._metrics = metrics
        self._tracer = tracer

    async def handle(
        self, requests: AsyncIterator[Request], context: Context
    ) -> AsyncIterator[Response]:
        metadata = {k: v for k, v in (context.invocation_metadata() or ()) if isinstance(v, str)}
        claims = await self._open(metadata, context)
        now = self._clock.now()
        stream = _Stream(
            str(uuid7(now)),
            claims,
            StreamTimers(self._gate.policy, opened_at=now, token_expires_at=claims.expires_at),
        )
        span = self._tracer.start_span(
            "grpc xype.v1.SessionService/Session", context=extract(metadata), kind=SpanKind.SERVER
        )
        span.set_attribute("xype.session_id", stream.session_id)
        inbox: asyncio.Queue[Request] = asyncio.Queue(maxsize=INBOX)
        reader = asyncio.create_task(_read(requests, inbox))
        started = time.monotonic()
        self._metrics.active.inc()
        try:
            with structlog.contextvars.bound_contextvars(
                session_id=stream.session_id, correlation_id=_correlation_id(metadata)
            ):
                logger.info("session.opened", user_id=str(claims.principal.user_id))
                async for out in self._loop(stream, inbox, reader):
                    yield out
        except Exception as bug:  # noqa: BLE001 - the transport boundary: the stream ends
            # Type only, never the message (rule 08); grpcio would log it with a traceback.
            logger.error("session.unhandled_error", error=type(bug).__name__)
            stream.reason = "internal"
        finally:
            reader.cancel()
            try:
                await _finish(reader)
            finally:
                # Recorded even if a second cancellation (shutdown) interrupts the wait.
                self._closed(stream, span, time.monotonic() - started)

    async def _open(self, metadata: dict[str, str], context: Context) -> AccessClaims:
        header = metadata.get("authorization", "")
        try:
            if not header.lower().startswith(_BEARER):
                raise InvalidTokenError
            claims = await self._gate.open(header[len(_BEARER) :].strip())
        except InvalidTokenError:
            await self._refuse(context, grpc.StatusCode.UNAUTHENTICATED, "invalid_token")
        except RateLimitedError:
            await self._refuse(context, grpc.StatusCode.RESOURCE_EXHAUSTED, "rate_limited")
        except DependencyUnavailableError:
            await self._refuse(context, grpc.StatusCode.UNAVAILABLE, "unavailable")
        except Exception as bug:  # noqa: BLE001 - the transport boundary: the call ends
            logger.error("session.unhandled_error", error=type(bug).__name__)
            await self._refuse(context, grpc.StatusCode.INTERNAL, "internal")
        self._metrics.opens.labels("ok").inc()
        return claims

    async def _refuse(self, context: Context, code: grpc.StatusCode, outcome: str) -> NoReturn:
        self._metrics.opens.labels(outcome).inc()
        logger.info("session.open.refused", outcome=outcome)
        await context.abort(code, outcome)

    async def _loop(
        self, stream: _Stream, inbox: asyncio.Queue[Request], reader: asyncio.Task[None]
    ) -> AsyncIterator[Response]:
        while stream.reason is None:
            now = self._clock.now()
            due = stream.timers.due(now)
            if due is Due.REFRESH:
                expires = stream.timers.token_expires_at
                yield messages.token_refresh(now, stream.session_id, expires)
                continue
            if due is not None:
                code, text = _CLOSES[due]
                stream.reason = due.value
                yield messages.stream_error(now, stream.session_id, code, text)
                return
            request = await _next(inbox, reader, stream.timers.next_check(now))
            if request is _Wait.TIMEOUT:
                continue
            if request is _Wait.ENDED:
                failed = not reader.cancelled() and reader.exception() is not None
                stream.reason = "read_failed" if failed else "client_closed"
                return
            yield await self._handle(stream, request)

    async def _handle(self, stream: _Stream, request: Request) -> Response:
        body = messages.kind(request)
        correlation_id = messages.correlation_of(request, stream.session_id)
        stream.timers.seen(self._clock.now())
        stream.messages += 1
        outcome, out = await self._outcome(stream, request, body, correlation_id)
        # Only a successful Hello stops the Hello deadline.
        if stream.open is not None:
            stream.timers.seen(self._clock.now(), hello=True)
        # Only an accepted message resets the count; refusals in between do not.
        if outcome == "rate_limited":
            stream.rate_limited += 1
        elif outcome == "ok":
            stream.rate_limited = 0
        if stream.rate_limited >= _RATE_LIMITED_IN_A_ROW:
            stream.reason = "rate_limited"
        self._metrics.messages.labels(body, outcome).inc()
        out.envelope.correlation_id = correlation_id
        return out

    async def _outcome(
        self, stream: _Stream, request: Request, body: str, correlation_id: str
    ) -> tuple[str, Response]:
        try:
            return "ok", await self._dispatch(stream, request, body)
        except RefusedError as refused:
            # Before a valid Hello there is nothing to keep; after it, only a key past its
            # age ends the stream (the client mints a new key and reconnects).
            if stream.open is None or refused.reason == KEY_UNUSABLE:
                stream.reason = "refused"
            return "refused", self._error(correlation_id, Code.CODE_INVALID_REQUEST, refused.reason)
        except RateLimitedError as limited:
            retry_ms = math.ceil(limited.retry_after_seconds * 1000)
            return "rate_limited", self._error(
                correlation_id, Code.CODE_RATE_LIMITED, "rate limited", retry_ms
            )
        except DependencyUnavailableError:
            stream.reason = "unavailable"
            return "unavailable", self._error(correlation_id, Code.CODE_INTERNAL, "unavailable")
        except Exception as bug:  # noqa: BLE001 - the transport boundary: the stream ends
            # Type only: an exception message may carry request content (rule 08).
            logger.error("session.unhandled_error", error=type(bug).__name__)
            stream.reason = "internal"
            return "internal", self._error(correlation_id, Code.CODE_INTERNAL, "internal error")

    def _error(
        self, correlation_id: str, code: session_pb2.StreamError.Code, text: str, retry_ms: int = 0
    ) -> Response:
        return messages.stream_error(self._clock.now(), correlation_id, code, text, retry_ms)

    async def _dispatch(self, stream: _Stream, request: Request, body: str) -> Response:
        now = self._clock.now()
        if body == "hello":
            if stream.open is not None:
                await self._gate.message(stream.open)  # counted like any other message
                raise RefusedError(HELLO_TWICE)
            hello = messages.hello_request(request.hello)
            stream.open = await self._gate.hello(stream.claims, hello, stream.session_id)
            policy = self._gate.policy
            ack = session_pb2.HelloAck(
                session_id=stream.session_id,
                heartbeat_interval_s=policy.heartbeat_interval_s,
                rate_limit_per_minute=policy.requests_per_minute,
            )
            return messages.response(
                now, stream.session_id, session_pb2.SessionResponse(hello_ack=ack)
            )
        if stream.open is None:
            raise RefusedError(HELLO_FIRST)
        await self._gate.message(stream.open, messages.repository_ids(request))
        if body == "heartbeat":
            beat = session_pb2.Heartbeat(seq=request.heartbeat.seq)
            return messages.response(
                now, stream.session_id, session_pb2.SessionResponse(heartbeat=beat)
            )
        raise RefusedError(UNSUPPORTED)

    def _closed(self, stream: _Stream, span: Span, seconds: float) -> None:
        reason = stream.reason or "cancelled"
        self._metrics.active.dec()
        self._metrics.closed.labels(reason).inc()
        self._metrics.duration.observe(seconds)
        logger.info(
            "session.closed",
            user_id=str(stream.claims.principal.user_id),
            session_id=stream.session_id,
            reason=reason,
            messages=stream.messages,
            duration_ms=round(seconds * 1000),
        )
        span.set_attribute("xype.close_reason", reason)
        if reason in {"unavailable", "internal"}:
            span.set_status(Status(StatusCode.ERROR))
        span.end()


def _correlation_id(metadata: dict[str, str]) -> str | None:
    value = metadata.get(CORRELATION_ID_HEADER, "")
    return value if is_valid_correlation_id(value) else None


async def _read(requests: AsyncIterator[Request], inbox: asyncio.Queue[Request]) -> None:
    """Moves client messages into the inbox. It ends with the client's side of the
    stream, or with its error; `_next` notices either."""
    async for request in requests:
        await inbox.put(request)


async def _next(
    inbox: asyncio.Queue[Request], reader: asyncio.Task[None], wait_s: float
) -> Request | _Wait:
    """The next client message, or why there is none yet."""
    if inbox.empty() and reader.done():
        return _Wait.ENDED
    getter = asyncio.ensure_future(inbox.get())
    try:
        await asyncio.wait({getter, reader}, timeout=wait_s, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Also when this stream is cancelled while waiting: no getter is left behind.
        if not getter.done():
            getter.cancel()
    if getter.done() and not getter.cancelled():
        return getter.result()
    return _Wait.TIMEOUT


async def _finish(reader: asyncio.Task[None]) -> None:
    """Collects the reader's end, so no task error goes unretrieved; logs its type only."""
    (result,) = await asyncio.gather(reader, return_exceptions=True)
    if isinstance(result, Exception):
        logger.info("session.read_failed", error=type(result).__name__)
