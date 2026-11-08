"""Fixtures shared by the whole test suite."""

import logging
from collections.abc import Iterator

import pytest
import structlog


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
