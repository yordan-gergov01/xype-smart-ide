from collections.abc import Callable, Sequence

import pytest
from support.backend import FakeProbe, Harness
from support.probe_contract import assert_reachable, assert_unreachable

from backend.services.health import DependencyProbe, HealthService

type HarnessFactory = Callable[[Sequence[DependencyProbe]], Harness]


async def test_fake_probe_meets_the_probe_contract() -> None:
    await assert_reachable(FakeProbe("database"))
    await assert_unreachable(FakeProbe("database", reachable=False))


async def test_readiness_is_ok_when_every_probe_answers() -> None:
    service = HealthService([FakeProbe("database"), FakeProbe("redis")], timeout_seconds=1)
    readiness = await service.readiness()
    assert readiness.ready
    assert readiness.dependencies == {"database": "ok", "redis": "ok"}


async def test_unavailable_and_slow_probes_are_unavailable() -> None:
    service = HealthService(
        [
            FakeProbe("database", reachable=False),
            FakeProbe("redis", delay=1),
            FakeProbe("broker"),
        ],
        timeout_seconds=0.05,
    )
    readiness = await service.readiness()
    assert not readiness.ready
    assert readiness.dependencies == {
        "database": "unavailable",
        "redis": "unavailable",
        "broker": "ok",
    }


async def test_a_bug_in_a_probe_is_not_hidden_as_unavailable() -> None:
    service = HealthService(
        [FakeProbe("database", bug=TypeError()), FakeProbe("redis", delay=5)], timeout_seconds=10
    )
    with pytest.raises(ExceptionGroup) as raised:
        await service.readiness()
    assert raised.group_contains(TypeError)


async def test_healthz_answers_without_checking_dependencies(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory([FakeProbe("database", reachable=False)])
    async with harness.client() as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_each_dependency(harness_factory: HarnessFactory) -> None:
    harness = harness_factory([FakeProbe("database"), FakeProbe("broker", reachable=False)])
    async with harness.client() as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "dependencies": {"database": "ok", "broker": "unavailable"},
    }


async def test_readyz_is_200_when_ready(healthy: Harness) -> None:
    async with healthy.client() as client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_metrics_use_route_templates_and_known_methods(healthy: Harness) -> None:
    async with healthy.client() as client:
        await client.get("/healthz")
        await client.get("/no/such/path/42")
        await client.request("BREWCOFFEE", "/healthz")
        body = (await client.get("/metrics")).text
    assert 'xype_http_requests_total{method="GET",route="/healthz",status="200"} 1.0' in body
    assert 'route="<unmatched>",status="404"' in body
    assert 'method="_OTHER"' in body
    assert "/no/such/path/42" not in body
    assert "BREWCOFFEE" not in body
    assert "xype_http_request_duration_seconds_bucket" in body
