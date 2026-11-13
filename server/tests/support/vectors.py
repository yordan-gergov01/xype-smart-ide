"""`FakeVectorStore`: brute-force cosine in memory, with the checks `PgVectorStore`
makes (support/contracts/vectors.py)."""

import math
import uuid
from collections.abc import Sequence

from common_lib.domain import ModelRef
from common_lib.errors import InvalidValueError
from common_lib.vectors import MAX_K, SearchHit, VectorRecord, check_vector, check_write


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class FakeVectorStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, str], VectorRecord] = {}

    async def upsert(self, repository_id: uuid.UUID, records: Sequence[VectorRecord]) -> None:
        check_write(repository_id, records)
        for record in records:
            self.rows[(record.chunk.repository_id, record.chunk.leaf_node_id)] = record

    async def delete(self, repository_id: uuid.UUID, leaf_node_ids: Sequence[str]) -> None:
        check_write(repository_id, (), leaf_node_ids)
        for leaf in leaf_node_ids:
            self.rows.pop((repository_id, leaf), None)

    async def search(
        self,
        vector: Sequence[float],
        *,
        repository_ids: Sequence[uuid.UUID],
        model: ModelRef,
        k: int,
        min_score: float | None = None,
    ) -> Sequence[SearchHit]:
        check_vector(vector)
        if not 1 <= k <= MAX_K:
            raise InvalidValueError(f"k is between 1 and {MAX_K}")
        wanted = set(repository_ids)
        hits = [
            SearchHit(r.chunk, _cosine(vector, r.vector))
            for r in self.rows.values()
            if r.chunk.repository_id in wanted and r.model == model
        ]
        hits.sort(key=lambda h: -h.score)
        return tuple(h for h in hits[:k] if min_score is None or h.score >= min_score)
