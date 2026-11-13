"""The vector store seam (ADR-0020): pgvector now, a hosted vector database later."""

import uuid
from collections.abc import Sequence
from typing import Protocol

from common_lib.domain import ModelRef
from common_lib.vectors import SearchHit, VectorRecord


class VectorStore(Protocol):
    async def upsert(self, repository_id: uuid.UUID, records: Sequence[VectorRecord]) -> None:
        """Writes each record under `(repository_id, leaf_node_id)`; a repeated delivery
        replaces the row. Refuses a record of another repository (`InvalidValueError`), so
        a nested `ChunkRef` cannot write outside the authorised repository. At most
        `MAX_UPSERT` records; a later duplicate leaf wins."""
        ...

    async def delete(self, repository_id: uuid.UUID, leaf_node_ids: Sequence[str]) -> None:
        """Deletes those leaves only; unknown ids are ignored. At most `MAX_UPSERT` ids,
        each 64-char HMAC hex."""
        ...

    async def search(
        self,
        vector: Sequence[float],
        *,
        repository_ids: Sequence[uuid.UUID],
        model: ModelRef,
        k: int,
        min_score: float | None = None,
    ) -> Sequence[SearchHit]:
        """At most `k` hits from `repository_ids` embedded by `model`, best first, each
        with a score of at least `min_score` when one is given. The store does not check
        teams: callers pass only ids authorised for the principal (Hello or
        `RepositoryAccess`)."""
        ...
