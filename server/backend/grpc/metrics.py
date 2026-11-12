"""RED metrics of the `Session` stream with fixed, low-cardinality labels (rule 08): the
message kind is the `oneof` field name, never an id."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class StreamMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.active = Gauge("xype_grpc_streams_active", "Open Session streams.", registry=registry)
        self.opens = Counter(
            "xype_grpc_stream_opens_total",
            "Session stream opens by outcome (ok, invalid_token, rate_limited, unavailable).",
            ["outcome"],
            registry=registry,
        )
        self.messages = Counter(
            "xype_grpc_stream_messages_total",
            "Session stream messages from clients by kind and outcome.",
            ["kind", "outcome"],
            registry=registry,
        )
        self.closed = Counter(
            "xype_grpc_streams_closed_total",
            "Closed Session streams by reason.",
            ["reason"],
            registry=registry,
        )
        self.duration = Histogram(
            "xype_grpc_stream_duration_seconds",
            "How long Session streams stay open.",
            buckets=(1, 10, 60, 300, 600, 900, 1800),
            registry=registry,
        )
