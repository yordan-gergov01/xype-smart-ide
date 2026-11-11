"""Backend app built with fake probes, fake auth stores and placeholder settings (no I/O)."""

import asyncio
import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import httpx
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.core.auth import peppers
from backend.core.settings import BackendSettings
from backend.main import create_app
from backend.services.auth import AuthDependencies, ClientCredentialVerifier
from backend.services.health import DependencyProbe
from common_lib.clock import Clock, SystemClock
from common_lib.errors import DependencyUnavailableError
from support.auth import (
    KEK,
    PEPPER,
    SIGNING_KEY,
    FakeRateLimiter,
    FakeRefreshTokenStore,
    FakeRevocationPublisher,
    FakeUserStore,
)
from support.keystore import FakeKeyStore
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
        "keystore_host": "keystore.invalid",
        "keystore_user": "backend",
        "keystore_password": "not-a-secret-keystore",
        "rabbitmq_host": "rabbitmq.invalid",
        "rabbitmq_vhost": "xype",
        "rabbitmq_user": "backend",
        "rabbitmq_password": "not-a-secret-broker",
        "log_format": "json",
        "readiness_timeout_seconds": 0.2,
        "tls_ca_file": ca_file(),
        "auth_signing_key_current": SIGNING_KEY,
        "auth_pepper_current": PEPPER,
        "kek_current": KEK,
    }
    values.update(overrides)
    return BackendSettings.model_validate(values)


@dataclass
class FakeAuth:
    """The auth feature's stores as fakes, wired like `backend.core.auth`."""

    settings: BackendSettings
    users: FakeUserStore = field(default_factory=FakeUserStore)
    limiter: FakeRateLimiter = field(default_factory=FakeRateLimiter)
    revocations: FakeRevocationPublisher = field(default_factory=FakeRevocationPublisher)
    clock: Clock = field(default_factory=SystemClock)
    tokens: FakeRefreshTokenStore = field(init=False)
    keys: FakeKeyStore = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = FakeRefreshTokenStore(self.users)
        self.keys = FakeKeyStore(self.clock)

    @property
    def deps(self) -> AuthDependencies:
        return AuthDependencies(
            verifier=ClientCredentialVerifier(self.users, peppers(self.settings)),
            users=self.users,
            tokens=self.tokens,
            limiter=self.limiter,
            revocations=self.revocations,
            keys=self.keys,
        )


class Harness:
    """An app plus everything a test needs to inspect what it emitted."""

    def __init__(
        self, probes: Sequence[DependencyProbe], logs: io.StringIO, **settings: object
    ) -> None:
        self.logs = logs
        self.settings = make_settings(**settings)
        self.auth = FakeAuth(self.settings)
        self.app = create_app(self.settings, probes=probes, auth=self.auth.deps)
        self.spans = InMemorySpanExporter()
        self.app.state.tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))

    def client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://backend.test")


def emitted(harness: Harness) -> str:
    """Every log line and every span the app produced, as one string."""
    logs: str = harness.logs.getvalue()
    spans = [
        json.dumps(
            {
                "name": s.name,
                "attributes": dict(s.attributes or {}),
                "events": [(e.name, dict(e.attributes or {})) for e in s.events],
                "status": s.status.description,
            }
        )
        for s in harness.spans.get_finished_spans()
    ]
    return logs + "\n".join(spans)


# Builds a `Harness` from probes and settings overrides (the unit conftest's fixture).
type HarnessFactory = Callable[..., Harness]
