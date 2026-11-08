"""Fixtures for the backend unit tests (fakes only, no I/O)."""

import io
from collections.abc import Callable, Sequence

import pytest
from support.backend import FakeProbe, Harness

from backend.services.health import DependencyProbe

type HarnessFactory = Callable[[Sequence[DependencyProbe]], Harness]


@pytest.fixture
def harness_factory(monkeypatch: pytest.MonkeyPatch) -> HarnessFactory:
    def build(probes: Sequence[DependencyProbe]) -> Harness:
        logs = io.StringIO()
        monkeypatch.setattr("sys.stdout", logs)
        return Harness(probes, logs)

    return build


@pytest.fixture
def healthy(harness_factory: HarnessFactory) -> Harness:
    return harness_factory([FakeProbe("database"), FakeProbe("redis"), FakeProbe("broker")])
