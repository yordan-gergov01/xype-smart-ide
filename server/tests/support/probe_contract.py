"""The contract every `DependencyProbe` meets: the fake and each real adapter run it."""

import pytest

from backend.services.health import DependencyProbe
from common_lib.errors import DependencyUnavailableError


async def assert_reachable(probe: DependencyProbe) -> None:
    """A reachable dependency: `check` returns."""
    await probe.check()


async def assert_unreachable(probe: DependencyProbe) -> None:
    """An unreachable dependency: `check` raises only `DependencyUnavailableError`,
    naming the dependency and carrying no driver error as its cause."""
    with pytest.raises(DependencyUnavailableError) as raised:
        await probe.check()
    assert raised.value.dependency == probe.name
    error = raised.value
    assert error.__cause__ is None
    # Raised `from None` inside an adapter, or with no driver error around at all.
    assert error.__context__ is None or error.__suppress_context__
