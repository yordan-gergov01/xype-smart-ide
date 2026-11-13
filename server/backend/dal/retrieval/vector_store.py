"""`VectorStore` on pgvector (ADR-0020). Writes go to the primary; searches use the read
sessions, an HNSW cosine index and iterative scans, so a selective repository filter
does not silently return fewer than `k` rows."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.dal.transactions import transaction
from common_lib.domain import ChunkRef, ModelRef
from common_lib.errors import InvalidValueError
from common_lib.models import CodeChunk
from common_lib.vectors import MAX_K, SearchHit, VectorRecord, check_vector, check_write

# Overwritten on a repeated delivery; `id` and `created_at` stay, so links survive.
_REPLACED = (
    "obfuscated_filename",
    "encrypted_chunk_hash",
    "line_start",
    "line_end",
    "embedding_model_id",
    "embedding_model_version",
    "vector_embedding",
)


def _row(record: VectorRecord) -> dict[str, Any]:  # reason: Any - column values
    chunk = record.chunk
    return {
        "repository_id": chunk.repository_id,
        "leaf_node_id": chunk.leaf_node_id,
        "obfuscated_filename": chunk.obfuscated_file_id,
        "encrypted_chunk_hash": chunk.chunk_hash,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "embedding_model_id": record.model.model_id,
        "embedding_model_version": record.model.model_version,
        "vector_embedding": list(record.vector),
    }


class PgVectorStore:
    def __init__(
        self,
        write_sessions: async_sessionmaker[AsyncSession],
        read_sessions: async_sessionmaker[AsyncSession],
        *,
        ef_search: int = 100,
    ) -> None:
        self._write = write_sessions
        self._read = read_sessions
        self._ef_search = ef_search

    async def upsert(self, repository_id: uuid.UUID, records: Sequence[VectorRecord]) -> None:
        check_write(repository_id, records)
        # One row per leaf: a later duplicate wins, as Postgres refuses two in one insert.
        rows = list(
            {(r.chunk.repository_id, r.chunk.leaf_node_id): _row(r) for r in records}.values()
        )
        if not rows:
            return
        statement = insert(CodeChunk).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["repository_id", "leaf_node_id"],
            set_={
                **{name: statement.excluded[name] for name in _REPLACED},
                "updated_at": func.now(),
            },
        )
        async with transaction(self._write) as session:
            await session.execute(statement)

    async def delete(self, repository_id: uuid.UUID, leaf_node_ids: Sequence[str]) -> None:
        check_write(repository_id, (), leaf_node_ids)
        if not leaf_node_ids:
            return
        statement = delete(CodeChunk).where(
            CodeChunk.repository_id == repository_id,
            CodeChunk.leaf_node_id.in_(list(leaf_node_ids)),
        )
        async with transaction(self._write) as session:
            await session.execute(statement)

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
        if not repository_ids:
            return ()
        query = search_query(vector, repository_ids=repository_ids, model=model, k=k)
        async with transaction(self._read) as session:
            # `SET LOCAL` takes no bind parameters; `set_config(..., true)` is the same.
            await session.execute(
                select(func.set_config("hnsw.ef_search", str(self._ef_search), True))
            )
            await session.execute(
                select(func.set_config("hnsw.iterative_scan", "relaxed_order", True))
            )
            rows = (await session.execute(query)).all()
        # `relaxed_order` may return rows slightly out of order.
        hits = sorted((_hit(row) for row in rows), key=lambda h: -h.score)
        return tuple(hit for hit in hits if min_score is None or hit.score >= min_score)


def search_query(
    vector: Sequence[float], *, repository_ids: Sequence[uuid.UUID], model: ModelRef, k: int
) -> Select[Any]:  # reason: Any - a row of mixed column types
    """The search statement: the `ChunkRef` columns and the distance, never the stored
    vector. Public so a test can `EXPLAIN` exactly what the adapter runs."""
    distance = CodeChunk.vector_embedding.cosine_distance(list(vector)).label("distance")
    return (
        select(
            CodeChunk.repository_id,
            CodeChunk.obfuscated_filename,
            CodeChunk.line_start,
            CodeChunk.line_end,
            CodeChunk.encrypted_chunk_hash,
            CodeChunk.leaf_node_id,
            distance,
        )
        .where(
            CodeChunk.repository_id.in_(list(repository_ids)),
            CodeChunk.embedding_model_id == model.model_id,
            CodeChunk.embedding_model_version == model.model_version,
        )
        .order_by(distance)
        .limit(k)
    )


def _hit(row: Row[Any]) -> SearchHit:  # reason: Any - a row of mixed column types
    chunk = ChunkRef(
        repository_id=row.repository_id,
        obfuscated_file_id=row.obfuscated_filename,
        line_start=row.line_start,
        line_end=row.line_end,
        chunk_hash=row.encrypted_chunk_hash,
        leaf_node_id=row.leaf_node_id,
    )
    return SearchHit(chunk, 1.0 - float(row.distance))
