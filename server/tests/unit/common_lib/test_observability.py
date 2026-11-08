import io
import json
import logging
from typing import NamedTuple

import pytest
import structlog
from pydantic import SecretStr, ValidationError

from common_lib.correlation import is_valid_correlation_id, new_correlation_id
from common_lib.logging_config import REDACTED, configure_logging
from common_lib.settings import ServiceSettings, format_errors, load_settings
from common_lib.tracing import configure_tracing, current_trace_id


class _Point(NamedTuple):
    x: int
    y: int


class _Settings(ServiceSettings):
    required_password: SecretStr
    port: int


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_sensitive_keys_are_redacted_at_any_depth_without_changing_the_caller() -> None:
    stream = io.StringIO()
    configure_logging(service="test", stream=stream)
    headers = {"Authorization": "Bearer t", "x_api_key": "k"}
    structlog.get_logger().info(
        "x",
        db_password="p",
        code="def f(): ...",
        status_code=200,
        nested={"auth_token": "t", "size": 3},
        items=[{"secret": "s"}, ("a", {"prompt": "p"})],
        point=_Point(1, 2),
        headers_seen=headers,
    )
    line = _lines(stream)[0]
    assert line["db_password"] == REDACTED
    assert line["code"] == REDACTED
    assert line["status_code"] == 200
    assert line["nested"] == {"auth_token": REDACTED, "size": 3}
    assert line["items"] == [{"secret": REDACTED}, ["a", {"prompt": REDACTED}]]
    assert line["point"] == [1, 2]
    assert line["headers_seen"] == {"Authorization": REDACTED, "x_api_key": REDACTED}
    assert headers == {"Authorization": "Bearer t", "x_api_key": "k"}
    assert line["service"] == "test"


def test_log_level_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    settings = ServiceSettings()
    assert settings.log_level == "DEBUG"
    assert settings.otel_exporter_otlp_endpoint is None


def test_standard_logging_exceptions_lose_their_message() -> None:
    stream = io.StringIO()
    configure_logging(service="test", stream=stream, log_format="console")
    try:
        raise RuntimeError("payload-in-message")
    except RuntimeError:
        logging.getLogger("lib").exception("failed")
    output = stream.getvalue()
    assert "builtins.RuntimeError" in output
    assert "payload-in-message" not in output


def test_settings_errors_name_the_variable_never_the_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PORT", "not-a-number-secret")
    with pytest.raises(SystemExit):
        load_settings(_Settings)
    err = capsys.readouterr().err
    assert "REQUIRED_PASSWORD" in err
    assert "PORT" in err
    assert "not-a-number-secret" not in err


def test_format_errors_counts_every_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PORT", "REQUIRED_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError) as exc:
        _Settings()  # type: ignore[call-arg]
    assert format_errors(exc.value).startswith("Configuration error: 2 ")


def test_correlation_ids() -> None:
    assert is_valid_correlation_id(new_correlation_id())
    assert not is_valid_correlation_id("has space")
    assert not is_valid_correlation_id("x" * 129)


def test_current_trace_id_inside_and_outside_a_span() -> None:
    assert current_trace_id() is None
    provider = configure_tracing(service="test", otlp_endpoint=None)
    with provider.get_tracer("t").start_as_current_span("s"):
        trace_id = current_trace_id()
    assert trace_id is not None
    assert len(trace_id) == 32
