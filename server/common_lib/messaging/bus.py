"""The `MessageBus` seam (ADR-0004, ADR-0015) and the index topology it works on.

The exchanges, queues and bindings are declared by the broker's own definitions
(deploy/docker/rabbitmq/entrypoint.sh), not by the services, which have no configure
rights; these names must match that file. Each queue `index.<q>` has its own exchange
`xype.index.<q>`, a retry exchange `xype.index.<q>.retry` with one delay queue per
retry, and a dead-letter exchange `xype.index.<q>.dlx` with `index.<q>.dlq`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from common_lib.errors import XypeError

CHUNKS = "index.chunks"
VECTORS = "index.vectors"
QUEUES = frozenset({CHUNKS, VECTORS})
# A failing message is retried after about 1, 2 and 4 minutes (ADR-0004), then
# dead-lettered. The broker's `x-delivery-limit` (5) only catches consumers that crash
# mid-message: RabbitMQ 4 does not count an explicit requeue.
MAX_RETRIES = 3
# Headers the bus sets and keeps; every other header of a message is dropped on a copy.
REASON_HEADER = "x-xype-reason"  # a dead letter's fixed reason, never a detail
ATTEMPT_HEADER = "x-xype-attempt"  # retries so far
DEADLINE_HEADER = "x-xype-expires-at"  # Unix ms by the publisher's clock; void after it (NTP)
TRACE_HEADERS = frozenset({"traceparent", "tracestate"})  # W3C trace context (rule 08)
HEADERS = frozenset({REASON_HEADER, ATTEMPT_HEADER, DEADLINE_HEADER, *TRACE_HEADERS})
# Why a message is dead-lettered: a fixed vocabulary, safe to log.
REASONS = frozenset(
    {
        "malformed",
        "expired",
        "key_unusable",
        "decryption_failed",
        "handler_error",
        "not_pending",
        "redelivery_limit",
    }
)


def exchange(queue: str) -> str:
    return f"xype.{queue}"


def retry_exchange(queue: str) -> str:
    return f"xype.{queue}.retry"


def retry_queue(queue: str, retry: int) -> str:
    return f"{queue}.retry.{retry}"


def dead_letter_exchange(queue: str) -> str:
    return f"xype.{queue}.dlx"


def dead_letter_queue(queue: str) -> str:
    return f"{queue}.dlq"


class RetryableError(XypeError):
    """A handler could not finish now (a store is down): the message is tried again
    later, after each of the `MAX_RETRIES` delays, then dead-lettered."""


@dataclass(frozen=True, slots=True)
class Delivery:
    body: bytes
    message_id: str
    correlation_id: str
    attempt: int  # 0 on the first delivery


# Returns None to acknowledge, or a reason from `REASONS` to dead-letter the message.
# Raising `RetryableError` or `DependencyUnavailableError` asks for a retry; any other
# error dead-letters the message as "handler_error". A handler runs inside the trace
# context the message was published in, and must be idempotent (at-least-once).
type Handler = Callable[[Delivery], Awaitable[str | None]]


class MessageBus(Protocol):
    async def publish(
        self,
        queue: str,
        body: bytes,
        *,
        message_id: str,
        correlation_id: str,
        ttl_ms: int | None = None,
    ) -> None:
        """Persistent, with the current trace context, and returns only after the
        broker confirmed it was routed to `queue`. A message with `ttl_ms` is void that
        long after it was first published, retries included. Raises
        `DependencyUnavailableError` otherwise (a full queue refuses the publish)."""
        ...

    async def consume(self, queue: str, handler: Handler, *, prefetch: int) -> None:
        """Hands each message to `handler`, one at a time per consumer (scale with more
        consumers), until cancelled."""
        ...

    async def close(self) -> None: ...
