"""Contract of `MessageBus` (ADR-0004, ADR-0015): the fake (unit) and RabbitMQ with the
compose file's topology (integration) both run it on `index.vectors`, published by the
indexer's user and consumed by the backend's."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging import (
    MAX_RETRIES,
    VECTORS,
    Delivery,
    Handler,
    MessageBus,
    RetryableError,
    dead_letter_queue,
)

# How many messages are waiting in a queue (the fake counts; the broker is asked).
type Depth = Callable[[str], Awaitable[int]]
# The producer publishes to `index.vectors` (the indexer's role), the consumer reads it
# (the backend's): no single user may do both.
type Check = Callable[[MessageBus, MessageBus, Depth], Awaitable[None]]
WAIT_S = 30.0


async def eventually(condition: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(WAIT_S):
        # The broker's queue depth has no event to wait on: polled.
        while not await condition():  # noqa: ASYNC110
            await asyncio.sleep(0.05)


@asynccontextmanager
async def consuming(bus: MessageBus, handler: Handler) -> AsyncIterator[None]:
    task = asyncio.create_task(bus.consume(VECTORS, handler, prefetch=1))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def check_a_message_is_delivered_once_and_acknowledged(
    producer: MessageBus, consumer: MessageBus, depth: Depth
) -> None:
    seen: list[Delivery] = []

    async def handler(delivery: Delivery) -> None:
        seen.append(delivery)

    await producer.publish(VECTORS, b'{"n": 1}', message_id="m-1", correlation_id="c-1")
    async with consuming(consumer, handler):

        async def handled() -> bool:
            return len(seen) == 1 and await depth(VECTORS) == 0

        await eventually(handled)
    [delivery] = seen
    assert (delivery.body, delivery.message_id, delivery.correlation_id) == (
        b'{"n": 1}',
        "m-1",
        "c-1",
    )
    assert delivery.attempt == 0


async def check_a_refused_message_is_dead_lettered(
    producer: MessageBus, consumer: MessageBus, depth: Depth
) -> None:
    before = await depth(dead_letter_queue(VECTORS))

    async def handler(_: Delivery) -> str:
        return "malformed"

    await producer.publish(VECTORS, b"{}", message_id="m-2", correlation_id="c-2")
    async with consuming(consumer, handler):

        async def moved() -> bool:
            return await depth(dead_letter_queue(VECTORS)) == before + 1

        await eventually(moved)
    assert await depth(VECTORS) == 0


async def check_a_retry_ends_at_the_delivery_limit(
    producer: MessageBus, consumer: MessageBus, depth: Depth
) -> None:
    before = await depth(dead_letter_queue(VECTORS))
    calls = 0

    async def handler(_: Delivery) -> None:
        nonlocal calls
        calls += 1
        raise RetryableError

    await producer.publish(VECTORS, b"{}", message_id="m-3", correlation_id="c-3")
    async with consuming(consumer, handler):

        async def given_up() -> bool:
            return await depth(dead_letter_queue(VECTORS)) == before + 1

        await eventually(given_up)
    assert calls == MAX_RETRIES + 1  # the first delivery and every retry
    assert await depth(VECTORS) == 0


async def check_an_unroutable_publish_fails(
    producer: MessageBus, consumer: MessageBus, depth: Depth
) -> None:
    with pytest.raises(DependencyUnavailableError):
        await producer.publish("no.such.queue", b"{}", message_id="m-4", correlation_id="c-4")
    assert await depth(VECTORS) == 0


async def check_an_expired_message_is_never_handled(
    producer: MessageBus, consumer: MessageBus, depth: Depth
) -> None:
    before = await depth(dead_letter_queue(VECTORS))
    calls = 0

    async def handler(_: Delivery) -> None:
        nonlocal calls
        calls += 1

    await producer.publish(VECTORS, b"{}", message_id="m-5", correlation_id="c-5", ttl_ms=100)
    await asyncio.sleep(0.5)
    async with consuming(consumer, handler):

        async def expired() -> bool:
            return await depth(dead_letter_queue(VECTORS)) == before + 1

        await eventually(expired)
    assert calls == 0


async def check_the_trace_context_travels_with_the_message(
    producer: MessageBus, consumer: MessageBus, depth: Depth
) -> None:
    tracer = TracerProvider().get_tracer("contract")
    seen: list[int] = []

    async def handler(_: Delivery) -> None:
        seen.append(trace.get_current_span().get_span_context().trace_id)

    with tracer.start_as_current_span("publish") as span:
        await producer.publish(VECTORS, b"{}", message_id="m-6", correlation_id="c-6")
        sent = span.get_span_context().trace_id
    async with consuming(consumer, handler):

        async def handled() -> bool:
            return len(seen) == 1

        await eventually(handled)
    assert seen == [sent]
    assert await depth(VECTORS) == 0


CHECKS: tuple[Check, ...] = (
    check_a_message_is_delivered_once_and_acknowledged,
    check_a_refused_message_is_dead_lettered,
    check_a_retry_ends_at_the_delivery_limit,
    check_an_unroutable_publish_fails,
    check_an_expired_message_is_never_handled,
    check_the_trace_context_travels_with_the_message,
)
