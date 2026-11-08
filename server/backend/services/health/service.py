"""Readiness: can the backend reach every dependency it needs to serve requests?"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import structlog

from common_lib.errors import DependencyUnavailableError

logger = structlog.get_logger(__name__)

type DependencyStatus = Literal["ok", "unavailable"]


class DependencyProbe(Protocol):
    """One dependency's reachability check.

    Raises `DependencyUnavailableError` when the dependency cannot be reached; any
    other exception is a bug in the probe and propagates.
    """

    name: str

    async def check(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Readiness:
    """The result of one readiness check."""

    ready: bool
    dependencies: dict[str, DependencyStatus]


class HealthService:
    """Runs every probe concurrently, each under its own timeout."""

    def __init__(self, probes: Sequence[DependencyProbe], *, timeout_seconds: float) -> None:
        self._probes = tuple(probes)
        self._timeout = timeout_seconds

    async def readiness(self) -> Readiness:
        # A task group cancels the other probes when one has a bug and re-raises it.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._run(probe)) for probe in self._probes]
        dependencies = {p.name: t.result() for p, t in zip(self._probes, tasks, strict=True)}
        return Readiness(
            ready=all(s == "ok" for s in dependencies.values()), dependencies=dependencies
        )

    async def _run(self, probe: DependencyProbe) -> DependencyStatus:
        try:
            async with asyncio.timeout(self._timeout):
                await probe.check()
        except (DependencyUnavailableError, TimeoutError) as exc:
            logger.warning(
                "health.dependency_unavailable",
                dependency=probe.name,
                error_type=type(exc).__name__,
            )
            return "unavailable"
        return "ok"
