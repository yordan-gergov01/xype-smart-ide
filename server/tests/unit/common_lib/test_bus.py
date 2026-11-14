"""The in-memory bus meets the `MessageBus` contract; RabbitMQ runs the same checks in
tests/integration/test_bus.py."""

import pytest
from support.bus import FakeBus
from support.contracts import bus as contract

from common_lib.messaging import ATTEMPT_HEADER, MAX_RETRIES, VECTORS, Delivery
from common_lib.messaging.rabbit import _attempt


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_fake_meets_the_contract(check: contract.Check) -> None:
    bus = FakeBus()

    async def depth(queue: str) -> int:
        return bus.depth(queue)

    await check(bus, bus, depth)


async def test_a_dead_letter_keeps_a_fixed_reason_only() -> None:
    bus = FakeBus()

    async def handler(_: Delivery) -> str:
        return "the chunk at src/app.py line 3 had a token"  # not a reason: replaced

    await bus.publish(VECTORS, b"{}", message_id="m", correlation_id="c")
    async with contract.consuming(bus, handler):

        async def moved() -> bool:
            return bus.reasons(VECTORS) == ["handler_error"]

        await contract.eventually(moved)


@pytest.mark.parametrize(
    ("headers", "attempt"),
    [
        ({}, 0),
        ({ATTEMPT_HEADER: 2}, 2),
        ({ATTEMPT_HEADER: MAX_RETRIES}, MAX_RETRIES),
        ({ATTEMPT_HEADER: MAX_RETRIES + 1}, None),
        ({ATTEMPT_HEADER: -1}, None),
        ({ATTEMPT_HEADER: "1"}, None),
        ({ATTEMPT_HEADER: True}, None),
    ],
)
def test_a_forged_attempt_count_is_malformed(
    headers: dict[str, object], attempt: int | None
) -> None:
    """A count no bus wrote is dead-lettered, so it can neither reset nor skip retries."""
    assert _attempt(headers) == attempt
