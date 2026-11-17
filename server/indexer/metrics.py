"""The indexer's metrics (rule 08): fixed, low-cardinality labels only."""

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram

# What happened to a chunk: a vector, a rejection, or a dead letter's reason.
OUTCOMES = (
    "embedded",
    "secret_found",
    "too_large",
    "unscannable",
    "unembeddable",
    "malformed",
    "key_unusable",
    "decryption_failed",
)


@dataclass(frozen=True, slots=True)
class IndexerMetrics:
    chunks: Counter
    duration: Histogram

    @classmethod
    def create(cls, registry: CollectorRegistry) -> "IndexerMetrics":
        chunks = Counter(
            "xype_indexer_chunks_total",
            "Chunks handled, by outcome",
            ["outcome"],
            registry=registry,
        )
        for outcome in OUTCOMES:
            chunks.labels(outcome)
        duration = Histogram(
            "xype_indexer_chunk_duration_seconds",
            "Time from receiving a chunk to publishing its result",
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
            registry=registry,
        )
        return cls(chunks, duration)
