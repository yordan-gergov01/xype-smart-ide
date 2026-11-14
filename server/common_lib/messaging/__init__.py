"""Messaging: the `MessageBus` seam and its RabbitMQ adapter (ADR-0004, ADR-0015). Shared
by the backend and the indexer; it talks to the broker and to nothing else."""

from common_lib.messaging.bus import (
    ATTEMPT_HEADER,
    CHUNKS,
    DEADLINE_HEADER,
    HEADERS,
    MAX_RETRIES,
    QUEUES,
    REASON_HEADER,
    REASONS,
    TRACE_HEADERS,
    VECTORS,
    Delivery,
    Handler,
    MessageBus,
    RetryableError,
    dead_letter_exchange,
    dead_letter_queue,
    exchange,
    retry_exchange,
    retry_queue,
)
from common_lib.messaging.rabbit import RabbitBus, connect_bus

__all__ = [
    "ATTEMPT_HEADER",
    "CHUNKS",
    "DEADLINE_HEADER",
    "HEADERS",
    "MAX_RETRIES",
    "QUEUES",
    "REASONS",
    "REASON_HEADER",
    "TRACE_HEADERS",
    "VECTORS",
    "Delivery",
    "Handler",
    "MessageBus",
    "RabbitBus",
    "RetryableError",
    "connect_bus",
    "dead_letter_exchange",
    "dead_letter_queue",
    "exchange",
    "retry_exchange",
    "retry_queue",
]
