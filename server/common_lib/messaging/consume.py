"""Keeps a consumer running across broker outages (ADR-0004): shared by the indexer and
the backend's `index.vectors` consumer, each in its own composition root."""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import structlog

from common_lib.errors import DependencyUnavailableError
from common_lib.messaging.bus import MessageBus

# Waits between restarts: 1 s, 2 s, ... up to 30 s.
RESTART_MIN_S, RESTART_MAX_S = 1.0, 30.0

logger = structlog.get_logger(__name__)


async def consume_forever(
    connect: Callable[[], Awaitable[MessageBus]],
    run: Callable[[MessageBus], Awaitable[None]],
    consuming: Callable[[bool], None],
    *,
    restart_min_s: float = RESTART_MIN_S,
) -> None:
    """Connects and runs until cancelled. A broker failure (`DependencyUnavailableError`)
    closes the bus, waits and starts again; `consuming` reports whether a bus is up, for
    `/readyz`."""
    wait = restart_min_s
    while True:
        bus: MessageBus | None = None
        try:
            bus = await connect()
            consuming(True)
            wait = restart_min_s
            await run(bus)
        except DependencyUnavailableError:
            logger.warning("consumer.restarting", wait_s=wait)
        finally:
            consuming(False)
            if bus is not None:
                with contextlib.suppress(DependencyUnavailableError, OSError):
                    await bus.close()
        await asyncio.sleep(wait)
        wait = min(wait * 2, RESTART_MAX_S)
