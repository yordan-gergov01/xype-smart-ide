"""Settings base shared by every service (ADR-0010).

Each service defines one settings class that inherits from `ServiceSettings` and
creates it once at startup through `load_settings`. Values come only from
environment variables; nothing else in the code reads `os.environ`.
"""

import sys

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common_lib.logging_config import LogFormat, LogLevel


class ServiceSettings(BaseSettings):
    """Base class for a service's settings: environment variables only, immutable."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        # Settings hold secrets: a validation error must never repeat the rejected value.
        hide_input_in_errors=True,
    )

    log_level: LogLevel = "INFO"
    # JSON lines in containers; "console" for readable local output.
    log_format: LogFormat = "json"
    # OTLP/HTTP base URL of the collector, e.g. http://otel-collector:4318. Empty: no export.
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("otel_exporter_otlp_endpoint", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value


class PostgresSettings(ServiceSettings):
    """Connection to Postgres with one role (`DB_*`). Hosts have no default."""

    db_host: str
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str
    db_user: str
    db_password: SecretStr


def load_settings[SettingsT: ServiceSettings](settings_cls: type[SettingsT]) -> SettingsT:
    """Create the settings or stop the process with a clear, value-free message.

    A missing or invalid variable is reported by its name and the reason, never by
    its value, so a mistyped secret cannot leak into the logs.
    """
    try:
        return settings_cls()
    except ValidationError as exc:
        print(format_errors(exc), file=sys.stderr)  # noqa: T201 - logging is not configured yet
        raise SystemExit(1) from None


def format_errors(exc: ValidationError) -> str:
    """One line per invalid variable: its environment name and the reason."""
    errors = exc.errors(include_input=False, include_url=False)
    lines = [f"Configuration error: {len(errors)} environment variable(s) missing or invalid."]
    for error in errors:
        name = ".".join(str(part) for part in error["loc"]).upper() or "<settings>"
        lines.append(f"  - {name}: {error['msg']}")
    return "\n".join(lines)
