"""Nothing a client sends, and no exception message, reaches logs, spans or responses.

.claude/rules/07-security-privacy.md and 08-observability.md: logs and traces carry
metadata only. This suite is the guard; add a case for every new transport path.
"""

import contextlib
import json
from collections.abc import AsyncIterator

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from opentelemetry.trace import StatusCode
from support.backend import Harness, emitted

SECRETS = (
    "SECRET-BODY-def-add-a-b",
    "SECRET-QUERY-refactor-this",
    "SECRET-TOKEN-abc123",
    "SECRET-PATH-src-billing-py",
    "SECRET-EXCEPTION-MESSAGE",
)


@pytest.fixture
def leaky(healthy: Harness) -> Harness:
    """Adds routes that receive secrets and fail with a secret in the exception."""

    async def echo_length(item_id: str, request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body()) + len(item_id)}

    async def explode() -> None:
        raise ValueError(SECRETS[4])

    async def explode_mid_stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial"
            raise ValueError(SECRETS[4])

        return StreamingResponse(chunks())

    healthy.app.add_api_route("/items/{item_id}", echo_length, methods=["POST"])
    healthy.app.add_api_route("/explode", explode, methods=["POST"])
    healthy.app.add_api_route("/explode-mid-stream", explode_mid_stream, methods=["GET"])
    return healthy


async def test_bodies_queries_paths_and_headers_never_reach_logs_or_spans(leaky: Harness) -> None:
    async with leaky.client() as client:
        response = await client.post(
            f"/items/{SECRETS[3]}",
            params={"q": SECRETS[1]},
            content=SECRETS[0],
            headers={"authorization": f"Bearer {SECRETS[2]}"},
        )
    assert response.status_code == 200
    output = emitted(leaky)
    assert '"route": "/items/{item_id}"' in output
    for secret in SECRETS[:4]:
        assert secret not in output


async def test_exception_messages_never_reach_logs_spans_or_the_client(leaky: Harness) -> None:
    async with leaky.client() as client:
        response = await client.post("/explode")
    assert response.status_code == 500
    assert SECRETS[4] not in response.text
    assert response.json() == {"code": "INTERNAL_ERROR", "detail": "Internal server error."}
    output = emitted(leaky)
    assert "builtins.ValueError" in output
    assert SECRETS[4] not in output


async def test_correlation_id_is_echoed_or_replaced(healthy: Harness) -> None:
    async with healthy.client() as client:
        # A non-probe route, so the access line is logged at INFO.
        echoed = await client.get("/nope", headers={"x-correlation-id": "abc-123"})
        replaced = await client.get("/nope", headers={"x-correlation-id": "bad id"})
    assert echoed.headers["x-correlation-id"] == "abc-123"
    assert len(replaced.headers["x-correlation-id"]) == 32
    log_lines = [json.loads(line) for line in healthy.logs.getvalue().splitlines() if line]
    assert any(line.get("correlation_id") == "abc-123" for line in log_lines)


async def test_failure_after_the_response_started_is_recorded_as_500(leaky: Harness) -> None:
    async with leaky.client() as client:
        # The server drops the connection mid-body; the client error is expected.
        with contextlib.suppress(Exception):
            await client.get("/explode-mid-stream")
    (span,) = [s for s in leaky.spans.get_finished_spans() if "explode" in s.name]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.events == ()
    assert '"status": 500' in leaky.logs.getvalue()
    assert SECRETS[4] not in emitted(leaky)
