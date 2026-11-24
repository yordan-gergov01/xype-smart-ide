"""`consume_forever`: a consumer that starts again after the broker went away, and says
it is not consuming meanwhile (ADR-0004)."""

import asyncio
import contextlib

from support.bus import FakeBus

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging import MessageBus, consume_forever


async def test_the_consumer_starts_again_after_the_broker_went_away() -> None:
    buses, runs = [FakeBus(), FakeBus()], 0
    states: list[bool] = []
    consuming = [False]
    restarted = asyncio.Event()

    async def connect() -> MessageBus:
        if not buses:
            raise DependencyUnavailableError("broker")
        return buses.pop(0)

    async def run(_: MessageBus) -> None:
        nonlocal runs
        runs += 1
        states.append(consuming[0])
        if runs == 2:
            restarted.set()
        if runs < 3:
            raise DependencyUnavailableError("broker")
        await asyncio.Event().wait()  # consumes until cancelled

    def report(up: bool) -> None:
        consuming[0] = up

    task = asyncio.create_task(consume_forever(connect, run, report, restart_min_s=0.01))
    await asyncio.wait_for(restarted.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert states[:2] == [True, True]
    assert not consuming[0]
