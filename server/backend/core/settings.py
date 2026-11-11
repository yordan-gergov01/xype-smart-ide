"""Backend settings, from environment variables only (ADR-0010).

Hosts have no defaults: where a dependency lives is deployment configuration.
"""

from pydantic import Field, SecretStr

from common_lib.settings import PostgresSettings


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
