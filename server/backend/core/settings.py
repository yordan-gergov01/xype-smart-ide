"""Backend settings, from environment variables only (ADR-0010).

Hosts have no defaults: where a dependency lives is deployment configuration.
"""

from pydantic import Field, SecretStr, field_validator

from common_lib.settings import KeyedSecret, PostgresSettings

DAY = 86_400


class BackendSettings(PostgresSettings):
    """Everything the backend reads from its environment (`DB_USER`: the runtime role)."""

    db_pool_size: int = Field(default=5, ge=1, le=50)
    # A read replica for queries that tolerate replica lag; the primary when unset.
    db_read_host: str | None = None
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    db_command_timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    redis_host: str
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_user: str
    redis_password: SecretStr
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=60)

    rabbitmq_host: str
    # AMQPS: the broker has no plaintext listener (ADR-0036).
    rabbitmq_port: int = Field(default=5671, ge=1, le=65535)
    rabbitmq_vhost: str
    rabbitmq_user: str
    rabbitmq_password: SecretStr

    # Each dependency must answer the readiness check within this time.
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Auth (ADR-0014). Keys as `<id>:<base64url>`: `python -m backend.admin_cli new-key`.
    # The previous key and pepper only verify, during a rotation.
    auth_signing_key_current: KeyedSecret
    auth_signing_key_previous: KeyedSecret | None = None
    auth_pepper_current: KeyedSecret
    auth_pepper_previous: KeyedSecret | None = None
    auth_issuer: str = "xype"
    # At most 15 minutes: revocation, rotation and the runbook rely on it (ADR-0014).
    auth_access_ttl_s: int = Field(default=900, ge=60, le=900)
    auth_refresh_ttl_s: int = Field(default=30 * DAY, ge=DAY, le=90 * DAY)
    auth_family_max_age_s: int = Field(default=90 * DAY, ge=DAY, le=365 * DAY)
    # Requests per user and minute, REST calls and stream messages alike.
    rate_limit_per_minute: int = Field(default=200, ge=1, le=100_000)
    # Failed logins per client id and minute before it must wait.
    auth_login_failures_per_minute: int = Field(default=10, ge=1, le=1000)

    @field_validator("auth_signing_key_previous", "auth_pepper_previous", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value
