"""Backend settings, from environment variables only (ADR-0010).

Hosts have no defaults: where a dependency lives is deployment configuration.
"""

from pydantic import Field, FilePath, SecretStr, field_validator, model_validator

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

    # The non-evicting key store for session keys, on its own network (ADR-0015).
    keystore_host: str
    keystore_port: int = Field(default=6379, ge=1, le=65535)
    keystore_user: str
    keystore_password: SecretStr

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

    # Session keys (ADR-0015): wrapped under the KEK in the key store; the previous KEK
    # only unwraps, during a rotation (docs/runbooks/key-rotation.md).
    kek_current: KeyedSecret
    kek_previous: KeyedSecret | None = None
    session_keys_per_minute: int = Field(default=10, ge=1, le=1000)
    # Minting one more revokes the user's oldest key.
    session_keys_max_active: int = Field(default=8, ge=1, le=64)

    # The gRPC Session stream (ADR-0013), served in the backend process with the
    # backend's certificate (ADR-0036). `host:port`; the edge proxy forwards to it.
    grpc_listen_address: str = Field(pattern=r"^[A-Za-z0-9.\-\[\]:]+:\d{1,5}$")
    tls_cert_file: FilePath
    tls_key_file: FilePath
    # The client sends a heartbeat this often; silence for `stream_idle_timeout_s`
    # closes the stream. Both stay below the edge proxy's 120 s gRPC timeouts.
    stream_heartbeat_interval_s: int = Field(default=20, ge=1, le=60)
    stream_idle_timeout_s: int = Field(default=60, ge=2, le=110)
    stream_hello_timeout_s: int = Field(default=10, ge=1, le=60)
    # TokenRefreshRequired goes out this long before the stream's access token expires.
    stream_refresh_lead_s: int = Field(default=60, ge=1, le=300)
    stream_max_repositories: int = Field(default=64, ge=1, le=1000)
    # Open streams per backend process (each holds up to ~3 MiB of messages).
    stream_max_concurrent: int = Field(default=32, ge=1, le=1000)
    # Streams in flight get this long to finish after SIGTERM.
    stream_shutdown_grace_s: float = Field(default=5.0, ge=0, le=10)
    # Merkle sync (ADR-0018): the server asks for every root this often after Hello; a
    # sync is forgotten after its deadline; a sync that lost the race to another one
    # starts over at most this often.
    merkle_poll_interval_s: int = Field(default=600, ge=30, le=3600)
    merkle_sync_deadline_s: int = Field(default=600, ge=30, le=3600)
    merkle_sync_max_restarts: int = Field(default=3, ge=0, le=10)
    # Nodes all syncs of one process may hold at once (a few hundred bytes each).
    merkle_sync_max_held_nodes: int = Field(default=1_000_000, ge=1000, le=5_000_000)

    @model_validator(mode="after")
    def _heartbeats_fit_the_idle_timeout(self) -> "BackendSettings":
        # Two heartbeats may be late before the stream counts as silent.
        if self.stream_idle_timeout_s < 2 * self.stream_heartbeat_interval_s:
            raise ValueError("stream_idle_timeout_s must be at least twice the heartbeat interval")
        return self

    @field_validator(
        "auth_signing_key_previous", "auth_pepper_previous", "kek_previous", mode="before"
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value
