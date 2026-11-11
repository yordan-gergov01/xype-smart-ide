"""`/api/v1/auth` over HTTP: shapes, fixed errors, headers, and that no secret or token
reaches a response, log line or span (rule 07, rule 08)."""

import httpx
import pytest
from support.backend import Harness, HarnessFactory, emitted

from backend.core.auth import client_admin
from common_lib.auth import NewClient
from common_lib.clock import SystemClock


async def new_client(harness: Harness) -> NewClient:
    admin = client_admin(harness.settings, harness.auth.deps, SystemClock())
    await admin.create_team("Web")
    return await admin.create_client(team="Web", email="d@x.test", display_name="D")


async def login(http: httpx.AsyncClient, client: NewClient) -> dict[str, object]:
    body = {"client_id": client.client_id, "client_secret": client.client_secret}
    response = await http.post("/api/v1/auth/token", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data: dict[str, object] = response.json()
    return data


async def test_token_refresh_and_me(healthy: Harness) -> None:
    client = await new_client(healthy)
    async with healthy.client() as http:
        pair = await login(http, client)
        assert set(pair) == {"access_token", "refresh_token", "expires_in", "token_type"}
        refreshed = await http.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert refreshed.status_code == 200
        me = await http.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
        )
    assert me.json() == {
        "user_id": str(client.user_id),
        "team_id": me.json()["team_id"],
        "role": "developer",
    }
    leaked = emitted(healthy)
    for value in (client.client_secret, pair["refresh_token"], pair["access_token"]):
        assert str(value) not in leaked
    assert client.client_id not in leaked


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"client_id": "xc_" + "0" * 16, "client_secret": "xcs_wrong"}, 401, "invalid_credentials"),
        ({"client_id": "not-an-id", "client_secret": "SECRET-xyz"}, 422, "invalid_request"),
        ({"client_id": "xc_" + "0" * 16}, 422, "invalid_request"),
        (
            {"client_id": "xc_" + "0" * 16, "client_secret": "s" * 4, "extra": 1},
            422,
            "invalid_request",
        ),
    ],
)
async def test_token_errors_are_fixed_and_never_echo_input(
    healthy: Harness, body: dict[str, object], status: int, code: str
) -> None:
    async with healthy.client() as http:
        response = await http.post("/api/v1/auth/token", json=body)
    assert (response.status_code, response.json()) == (status, {"error": code})
    for value in (v for v in body.values() if isinstance(v, str)):
        assert value not in response.text
        assert value not in emitted(healthy)


@pytest.mark.parametrize(
    "header", [None, "Basic abc", "Bearer ", "Bearer not.a.jwt", "bearer x.y.z"]
)
async def test_me_refuses_a_missing_or_bad_token(healthy: Harness, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    async with healthy.client() as http:
        response = await http.get("/api/v1/auth/me", headers=headers)
    assert (response.status_code, response.json()) == (401, {"error": "invalid_token"})
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_reused_refresh_token_is_refused(healthy: Harness) -> None:
    async with healthy.client() as http:
        pair = await login(http, await new_client(healthy))
        body = {"refresh_token": pair["refresh_token"]}
        assert (await http.post("/api/v1/auth/refresh", json=body)).status_code == 200
        again = await http.post("/api/v1/auth/refresh", json=body)
    assert (again.status_code, again.json()) == (401, {"error": "invalid_token"})


async def test_rate_limit_answers_429_with_retry_after(harness_factory: HarnessFactory) -> None:
    harness = harness_factory([], rate_limit_per_minute=2)
    async with harness.client() as http:
        pair = await login(http, await new_client(harness))
        headers = {"Authorization": f"Bearer {pair['access_token']}"}
        statuses = [(await http.get("/api/v1/auth/me", headers=headers)) for _ in range(3)]
    assert [r.status_code for r in statuses] == [200, 200, 429]
    assert statuses[-1].json() == {"error": "rate_limited"}
    assert int(statuses[-1].headers["retry-after"]) >= 1


async def test_fails_closed_with_503_when_redis_is_down(healthy: Harness) -> None:
    async with healthy.client() as http:
        pair = await login(http, await new_client(healthy))
        healthy.auth.limiter.down = True
        response = await http.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
        )
    assert (response.status_code, response.json()) == (503, {"error": "unavailable"})
