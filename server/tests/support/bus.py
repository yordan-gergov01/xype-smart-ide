"""`FakeBus`: the index queues in memory, with the routing, retries, deadlines,
dead-lettering and trace context `RabbitBus` gets from the broker and its headers
(support/contracts/bus.py). Retry delays are not modelled: a retry is due at once."""

import asyncio
import time
from collections import deque
from dataclasses import dataclass

from opentelemetry import context, propagate

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging import (
    MAX_RETRIES,
    QUEUES,
    REASONS,
    Delivery,
    Handler,
    RetryableError,
    dead_letter_queue,
)


@dataclass
class _Message:
    body: bytes
    message_id: str
    correlation_id: str
    deadline_ms: int | None
    trace: dict[str, str]
    attempt: int = 0
    reason: str | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


class FakeBus:
    def __init__(self) -> None:
        names = [*QUEUES, *(dead_letter_queue(q) for q in QUEUES)]
        self.queues: dict[str, deque[_Message]] = {name: deque() for name in names}
        self.closed = False

    async def publish(
        self,
        queue: str,
        body: bytes,
        *,
        message_id: str,
        correlation_id: str,
        ttl_ms: int | None = None,
    ) -> None:
        if queue not in QUEUES:
            raise DependencyUnavailableError("broker")
        trace: dict[str, str] = {}
        propagate.inject(trace)
        deadline = _now_ms() + ttl_ms if ttl_ms is not None else None
        self.queues[queue].append(_Message(body, message_id, correlation_id, deadline, trace))

    async def consume(self, queue: str, handler: Handler, *, prefetch: int) -> None:
        if queue not in QUEUES:
            raise DependencyUnavailableError("broker")
        while True:
            pending = self.queues[queue]
            if not pending:
                await asyncio.sleep(0.01)
                continue
            await self._handle(queue, pending.popleft(), handler)

    async def close(self) -> None:
        self.closed = True

    def depth(self, queue: str) -> int:
        return len(self.queues[queue])

    def reasons(self, queue: str) -> list[str | None]:
        return [m.reason for m in self.queues[dead_letter_queue(queue)]]

    def _dead_letter(self, queue: str, message: _Message, reason: str) -> None:
        message.reason = reason if reason in REASONS else "handler_error"
        self.queues[dead_letter_queue(queue)].append(message)

    async def _handle(self, queue: str, message: _Message, handler: Handler) -> None:
        if message.deadline_ms is not None and _now_ms() >= message.deadline_ms:
            self._dead_letter(queue, message, "expired")
            return
        delivery = Delivery(
            message.body, message.message_id, message.correlation_id, message.attempt
        )
        token = context.attach(propagate.extract(message.trace))
        try:
            reason = await handler(delivery)
        except (RetryableError, DependencyUnavailableError):
            message.attempt += 1
            if message.attempt > MAX_RETRIES:
                self._dead_letter(queue, message, "redelivery_limit")
            else:
                self.queues[queue].append(message)
            return
        except Exception:  # noqa: BLE001 - as RabbitBus: one message fails, not the consumer
            reason = "handler_error"
        finally:
            context.detach(token)
        if reason is not None:
            self._dead_letter(queue, message, reason)
