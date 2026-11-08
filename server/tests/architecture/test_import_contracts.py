"""Run the import-linter contracts from pyproject.toml as part of the test suite."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from importlinter.cli import EXIT_STATUS_SUCCESS, lint_imports

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture
def _keep_loggers_enabled() -> Iterator[None]:
    """import-linter calls logging.config.dictConfig, which disables every existing
    logger; re-enable them so later tests still see their output."""
    enabled = [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger) and not logger.disabled
    ]
    yield
    for logger in enabled:
        logger.disabled = False


@pytest.mark.usefixtures("_keep_loggers_enabled")
def test_import_contracts_hold() -> None:
    assert lint_imports(config_filename=str(PYPROJECT), no_cache=True) == EXIT_STATUS_SUCCESS
