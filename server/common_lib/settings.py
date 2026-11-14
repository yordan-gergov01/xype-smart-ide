"""Settings base shared by every service (ADR-0010).

Each service defines one settings class that inherits from `ServiceSettings` and
creates it once at startup through `load_settings`. Values come only from
environment variables; nothing else in the code reads `os.environ`.
"""

import base64
import binascii
import re
import secrets
import sys
from typing import Annotated

from pydantic import AfterValidator, Field, FilePath, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common_lib.logging_config import LogFormat, LogLevel
from common_lib.secret_bytes import SecretBytes

# `<id>:<32 random bytes, base64url>`: signing keys, peppers and KEKs (ADR-0014, ADR-0015).
_KEYED_SECRET = re.compile(r"^([a-z0-9][a-z0-9-]{0,31}):([A-Za-z0-9_-]{43}=?)$")
KEYED_SECRET_BYTES = 32


def _check_keyed_secret(value: SecretStr) -> SecretStr:
    parse_keyed_secret(value)
    return value


def parse_keyed_secret(value: SecretStr) -> tuple[str, SecretBytes]:
    """Split `<id>:<base64url>` into the id and the 32 key bytes.

    The error names the expected shape only, never the value.
    """
    match = _KEYED_SECRET.match(value.get_secret_value())
    if match is None:
        raise ValueError("expected '<id>:<32 bytes as base64url>'")
    try:
        key = base64.urlsafe_b64decode(match.group(2) + "=" * (-len(match.group(2)) % 4))
    except binascii.Error:
        raise ValueError("expected '<id>:<32 bytes as base64url>'") from None
    if len(key) != KEYED_SECRET_BYTES or len(set(key)) < KEYED_SECRET_BYTES // 4:
        raise ValueError("the key must be 32 random bytes")
    return match.group(1), SecretBytes(key)


# A secret with its id, validated at startup; `parse_keyed_secret` gives the bytes.
KeyedSecret = Annotated[SecretStr, AfterValidator(_check_keyed_secret)]


def new_keyed_secret(key_id: str) -> str:
    """A fresh `<id>:<base64url>` value for the environment (`admin_cli new-key`)."""
    key = secrets.token_bytes(KEYED_SECRET_BYTES)
    value = f"{key_id}:{base64.urlsafe_b64encode(key).decode().rstrip('=')}"
    parse_keyed_secret(SecretStr(value))
    return value


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


class TlsSettings(ServiceSettings):
    """The CA every TLS connection verifies its server against (ADR-0036).

    Required, with no plaintext fallback: a service without a CA does not start.
    """

    tls_ca_file: FilePath


class PostgresSettings(TlsSettings):
    """Connection to Postgres with one role (`DB_*`), over TLS. Hosts have no default."""

    db_host: str
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str
    db_user: str
    db_password: SecretStr


class RabbitMQSettings(TlsSettings):
    """Connection to RabbitMQ with one user (`RABBITMQ_*`), AMQPS only (ADR-0036)."""

    rabbitmq_host: str
    rabbitmq_port: int = Field(default=5671, ge=1, le=65535)
    rabbitmq_vhost: str
    rabbitmq_user: str
    rabbitmq_password: SecretStr
    rabbitmq_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)


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
