"""Backend settings, from environment variables only (ADR-0010).

Hosts have no defaults: where a dependency lives is deployment configuration.
"""

from pydantic import Field, SecretStr

from common_lib.settings import ServiceSettings


class BackendSettings(ServiceSettings):
    """Everything the backend reads from its environment."""

    db_host: str
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str
    db_user: str
    db_password: SecretStr
    db_pool_size: int = Field(default=5, ge=1, le=50)

    redis_host: str
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_user: str
    redis_password: SecretStr

    rabbitmq_host: str
    rabbitmq_port: int = Field(default=5672, ge=1, le=65535)
    rabbitmq_vhost: str
    rabbitmq_user: str
    rabbitmq_password: SecretStr

    # Each dependency must answer the readiness check within this time.
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
