"""Backend app built with fake probes and placeholder settings (no I/O)."""

import asyncio
import io
from collections.abc import Sequence

import httpx
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.core.settings import BackendSettings
from backend.main import create_app
from backend.services.health import DependencyProbe
from common_lib.errors import DependencyUnavailableError
from support.tls import ca_file


class FakeProbe:
    """In-memory probe: answers, is unavailable, hangs, or has a bug, as told."""

    def __init__(
        self,
        name: str,
        *,
        reachable: bool = True,
        delay: float = 0,
        bug: Exception | None = None,
    ) -> None:
        self.name = name
        self._reachable = reachable
        self._delay = delay
        self._bug = bug

    async def check(self) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._bug is not None:
            raise self._bug
        if not self._reachable:
            raise DependencyUnavailableError(self.name)


def make_settings(**overrides: object) -> BackendSettings:
    values: dict[str, object] = {
        "db_host": "db.invalid",
        "db_name": "xype",
        "db_user": "app",
        "db_password": "not-a-secret-db",
        "redis_host": "redis.invalid",
        "redis_user": "backend",
        "redis_password": "not-a-secret-redis",
        "rabbitmq_host": "rabbitmq.invalid",
        "rabbitmq_vhost": "xype",
        "rabbitmq_user": "backend",
        "rabbitmq_password": "not-a-secret-broker",
        "log_format": "json",
        "readiness_timeout_seconds": 0.2,
        "tls_ca_file": ca_file(),
    }
    values.update(overrides)
    return BackendSettings.model_validate(values)


class Harness:
    """An app plus everything a test needs to inspect what it emitted."""

    def __init__(self, probes: Sequence[DependencyProbe], logs: io.StringIO) -> None:
        self.logs = logs
        self.app = create_app(make_settings(), probes=probes)
        self.spans = InMemorySpanExporter()
        self.app.state.tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))

    def client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://backend.test")
