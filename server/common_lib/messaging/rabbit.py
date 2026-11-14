"""`MessageBus` on RabbitMQ (`aio_pika`), AMQPS only (ADR-0004, ADR-0036).

The service user has no configure rights, so nothing is declared: exchanges and queues
are looked up by name. Publishing waits for the broker's confirm and asks for the
message to be routed (`mandatory`), so a missing or full queue is an error, not a
silent drop. A retry is a copy in the queue's delay queue, which the broker returns
when its TTL ends: the consumer never sleeps. Bodies never reach a log line; a failing
handler is logged by error type only.
"""

import asyncio
import time
from datetime import timedelta

import aio_pika
import structlog
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection, FieldValue
from aio_pika.exceptions import AMQPError
from opentelemetry import context, propagate

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging.bus import (
    ATTEMPT_HEADER,
    DEADLINE_HEADER,
    MAX_RETRIES,
    REASON_HEADER,
    REASONS,
    TRACE_HEADERS,
    Delivery,
    Handler,
    RetryableError,
    dead_letter_exchange,
    exchange,
    retry_exchange,
    retry_queue,
)
from common_lib.settings import RabbitMQSettings
from common_lib.tls import client_context

logger = structlog.get_logger(__name__)

# A publish that the broker has not confirmed within this long counts as failed.
PUBLISH_TIMEOUT_S = 10.0
_RETRY = (RetryableError, DependencyUnavailableError)
type Headers = dict[str, FieldValue]


async def connect_bus(settings: RabbitMQSettings) -> "RabbitBus":
    """A robust connection (it reconnects on its own) for this service's user."""
    try:
        connection = await aio_pika.connect_robust(
            host=settings.rabbitmq_host,
            port=settings.rabbitmq_port,
            virtualhost=settings.rabbitmq_vhost,
            login=settings.rabbitmq_user,
            password=settings.rabbitmq_password.get_secret_value(),
            ssl=True,
            ssl_context=client_context(settings.tls_ca_file),
            timeout=settings.rabbitmq_connect_timeout_seconds,
        )
    except (AMQPError, OSError, TimeoutError):
        raise DependencyUnavailableError("broker") from None
    return RabbitBus(connection)


def _now_ms() -> int:
    return int(time.time() * 1000)


class RabbitBus:
    def __init__(self, connection: AbstractRobustConnection) -> None:
        self._connection = connection
        self._publisher: aio_pika.abc.AbstractChannel | None = None
        self._lock = asyncio.Lock()

    async def publish(
        self,
        queue: str,
        body: bytes,
        *,
        message_id: str,
        correlation_id: str,
        ttl_ms: int | None = None,
    ) -> None:
        trace: dict[str, str] = {}
        propagate.inject(trace)
        headers: Headers = {k: v for k, v in trace.items() if k in TRACE_HEADERS}
        if ttl_ms is not None:
            headers[DEADLINE_HEADER] = _now_ms() + ttl_ms
        message = aio_pika.Message(
            body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message_id,
            correlation_id=correlation_id,
            expiration=timedelta(milliseconds=ttl_ms) if ttl_ms is not None else None,
            headers=headers,
        )
        await self._send(exchange(queue), queue, message)

    async def consume(self, queue: str, handler: Handler, *, prefetch: int) -> None:
        channel: aio_pika.abc.AbstractChannel | None = None
        try:
            channel = await self._connection.channel()
            await channel.set_qos(prefetch_count=prefetch)
            source = await channel.get_queue(queue, ensure=False)
            async with source.iterator() as messages:
                async for message in messages:
                    await self._handle(queue, message, handler)
        except (AMQPError, OSError):
            raise DependencyUnavailableError("broker") from None
        finally:
            if channel is not None and not channel.is_closed:
                await channel.close()

    async def close(self) -> None:
        await self._connection.close()

    async def _handle(self, queue: str, message: AbstractIncomingMessage, handler: Handler) -> None:
        headers = message.headers or {}
        attempt = _attempt(headers)
        deadline = headers.get(DEADLINE_HEADER)
        if attempt is None or (deadline is not None and not isinstance(deadline, int)):
            await self._dead_letter(queue, message, "malformed")
        elif isinstance(deadline, int) and _now_ms() >= deadline:
            await self._dead_letter(queue, message, "expired")
        else:
            await self._run(queue, message, handler, attempt)
            return
        await message.ack()

    async def _run(
        self, queue: str, message: AbstractIncomingMessage, handler: Handler, attempt: int
    ) -> None:
        delivery = Delivery(
            message.body, message.message_id or "", message.correlation_id or "", attempt
        )
        trace = {k: str(v) for k, v in (message.headers or {}).items() if k in TRACE_HEADERS}
        token = context.attach(propagate.extract(trace))
        try:
            reason = await handler(delivery)
        except _RETRY:
            reason = None
            await self._retry(queue, message, attempt + 1)
        except Exception as bug:  # noqa: BLE001 - the worker's boundary: one message fails
            logger.error("messaging.handler_failed", queue=queue, error=type(bug).__name__)
            reason = "handler_error"
        finally:
            context.detach(token)
        if reason is not None:
            await self._dead_letter(queue, message, reason)
        await message.ack()

    async def _retry(self, queue: str, message: AbstractIncomingMessage, retry: int) -> None:
        """A copy waits in the queue's delay queue for this retry; after the last one
        the message is dead-lettered. The original is acknowledged by the caller."""
        if retry > MAX_RETRIES:
            await self._dead_letter(queue, message, "redelivery_limit")
            return
        copy = _copy(message, {ATTEMPT_HEADER: retry})
        await self._send(retry_exchange(queue), retry_queue(queue, retry), copy)

    async def _dead_letter(self, queue: str, message: AbstractIncomingMessage, reason: str) -> None:
        """A copy goes to the queue's `.dlq` with a fixed reason; only then is the
        original acknowledged, so a failure here leaves it to be delivered again."""
        if reason not in REASONS:
            reason = "handler_error"
        copy = _copy(message, {REASON_HEADER: reason})
        await self._send(dead_letter_exchange(queue), queue, copy)
        logger.warning("messaging.dead_lettered", queue=queue, reason=reason)

    async def _send(self, exchange_name: str, routing_key: str, message: aio_pika.Message) -> None:
        try:
            channel = await self._publish_channel()
            target = await channel.get_exchange(exchange_name, ensure=False)
            await target.publish(
                message, routing_key=routing_key, mandatory=True, timeout=PUBLISH_TIMEOUT_S
            )
        except (AMQPError, OSError, TimeoutError):
            raise DependencyUnavailableError("broker") from None

    async def _publish_channel(self) -> aio_pika.abc.AbstractChannel:
        async with self._lock:
            if self._publisher is None or self._publisher.is_closed:
                # An unroutable message raises instead of vanishing (`mandatory`).
                self._publisher = await self._connection.channel(
                    publisher_confirms=True, on_return_raises=True
                )
            return self._publisher


def _attempt(headers: object) -> int | None:
    """Retries so far; None for a value no bus wrote (it is dead-lettered as malformed,
    so a forged count can neither reset nor skip the retries)."""
    value = headers.get(ATTEMPT_HEADER, 0) if isinstance(headers, dict) else 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RETRIES:
        return None
    return value


def _copy(message: AbstractIncomingMessage, headers: Headers) -> aio_pika.Message:
    """The same body and ids, the trace context and the deadline, and the new headers;
    nothing else of the original. The broker drops a dead-lettered or retried message
    whose deadline passed (the per-message TTL is the time left)."""
    kept: Headers = {
        k: v
        for k, v in (message.headers or {}).items()
        if (k in TRACE_HEADERS and isinstance(v, str))
        or (k == DEADLINE_HEADER and isinstance(v, int))
    }
    deadline = kept.get(DEADLINE_HEADER)
    left = deadline - _now_ms() if isinstance(deadline, int) else None
    return aio_pika.Message(
        message.body,
        content_type=message.content_type,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        expiration=timedelta(milliseconds=max(left, 1)) if left is not None else None,
        headers={**kept, **headers},
    )
