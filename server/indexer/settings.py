"""Indexer settings, from environment variables only (ADR-0010). Hosts have no defaults."""

from pydantic import Field, FilePath

from common_lib.settings import (
    EmbeddingModelSettings,
    KekSettings,
    KeyStoreSettings,
    RabbitMQSettings,
)


class IndexerSettings(RabbitMQSettings, KeyStoreSettings, KekSettings, EmbeddingModelSettings):
    """The indexer reads `index.chunks`, the session keys (`GET` only) and the model
    server, and publishes `index.vectors`. It has no database (ADR-0015)."""

    # The embedder (ADR-0002): Ollama on the internal `models` network.
    ollama_base_url: str = Field(pattern=r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?/?$")
    embedding_timeout_s: float = Field(default=60.0, gt=0, le=600)
    # The shared scrub rules (ADR-0019), copied into the image.
    scrub_rules_file: FilePath
    # A 64 KiB chunk scans in tens of milliseconds; a scan still running after this is
    # killed and the chunk settled as unscannable (ADR-0019). Each scan process takes
    # about 31 MiB of the container's memory.
    scan_timeout_s: float = Field(default=2.0, gt=0, le=10)
    scan_workers: int = Field(default=2, ge=1, le=4)
    # Unacknowledged chunks this worker holds (it handles one at a time).
    prefetch: int = Field(default=4, ge=1, le=64)
    # Health and metrics over HTTP inside the internal networks: they carry no payload
    # (ADR-0036, like the OTel collector's metrics).
    metrics_listen_address: str = Field(
        default="0.0.0.0:9100",
        pattern=r"^[A-Za-z0-9.\-\[\]:]+:\d{1,5}$",
    )
