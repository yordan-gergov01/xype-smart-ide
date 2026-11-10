"""Fixtures shared by the whole test suite."""

import logging
from collections.abc import Iterator

import pytest
import structlog
from support import network_guard


def pytest_configure(config: pytest.Config) -> None:
    """Guard the network before any fixture runs, session-scoped ones included."""
    network_guard.install()


def pytest_unconfigure(config: pytest.Config) -> None:
    network_guard.uninstall()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Refusals outside any test (collection, session fixtures) fail the run too."""
    refused = network_guard.take_violations()
    if refused:
        print(*refused, sep="\n")  # noqa: T201 - pytest shows it with the result
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _no_outbound_connections() -> Iterator[None]:
    """Fail a test whose code reached for the network, even if it swallowed the error.

    Refusals left from a fixture with a wider scope fail the next test, not nobody.
    """
    leftover = network_guard.take_violations()
    if leftover:
        pytest.fail("before this test: " + "; ".join(leftover), pytrace=False)
    yield
    refused = network_guard.take_violations()
    if refused:
        pytest.fail("; ".join(refused), pytrace=False)


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Undo logging configuration after each test.

    Building an app configures logging with a handler bound to the current stream;
    a handler left behind would write to a closed stream in the next test.
    """
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
