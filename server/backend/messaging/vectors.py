"""The `index.vectors` consumer (ADR-0015, ADR-0018 amendment, slice 8c2): each result
of the indexer settles one pending leaf through `MerkleIndex.settle`.

A result for a leaf that is no longer pending with its hash (deleted or replaced by a
later sync, or never this repository's) is dead-lettered as `not_pending`; a repeated
delivery of a settled one is acknowledged. A store outage or a held lock is a retry.
Logs, spans and metrics carry ids, counts and fixed outcomes only; never a vector.
"""

from dataclasses import dataclass

import structlog
from opentelemetry.trace import Tracer
from prometheus_client import CollectorRegistry, Counter

from backend.services.indexing import MerkleIndex
from common_lib.errors import InvalidValueError
from common_lib.index_messages import decode_result
from common_lib.merkle import Settled
from common_lib.messaging import Delivery

logger = structlog.get_logger(__name__)

# What happened to a result: a `Settled` value, or `malformed`.
OUTCOMES: tuple[Settled | str, ...] = ("settled", "already_settled", "not_pending", "malformed")


@dataclass(frozen=True, slots=True)
class ResultMetrics:
    results: Counter

    @classmethod
    def create(cls, registry: CollectorRegistry) -> "ResultMetrics":
        results = Counter(
            "xype_index_results_total",
            "Indexer results handled by the backend, by outcome",
            ["outcome"],
            registry=registry,
        )
        for outcome in OUTCOMES:
            results.labels(outcome)
        return cls(results)


class VectorResults:
    def __init__(self, index: MerkleIndex, *, metrics: ResultMetrics, tracer: Tracer) -> None:
        self._index = index
        self._metrics = metrics
        self._tracer = tracer

    async def handle(self, delivery: Delivery) -> str | None:
        """The bus's handler: None acknowledges, a reason dead-letters (`MessageBus`);
        `DependencyUnavailableError` from the store asks for a retry."""
        # Exceptions are not recorded on the span: their text never reaches a trace.
        with self._tracer.start_as_current_span(
            "index.result", record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                result = decode_result(delivery.body).leaf_result()
                span.set_attribute("xype.repository_id", str(result.chunk.repository_id))
                # The store refuses a value it cannot hold (a CHECK) as invalid too.
                outcome: str = await self._index.settle(result)
            except InvalidValueError:
                outcome = "malformed"
            span.set_attribute("xype.outcome", outcome)
            self._metrics.results.labels(outcome).inc()
            # One line per result: routine outcomes only at debug (the counter has them).
            routine = outcome in ("settled", "already_settled")
            log = logger.debug if routine else logger.info
            log("index.result.handled", outcome=outcome, attempt=delivery.attempt)
            return None if routine else outcome
