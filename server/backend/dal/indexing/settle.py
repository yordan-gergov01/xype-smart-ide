"""Settling one leaf with the indexer's result (ADR-0018 amendment, slice 8c2), inside
the caller's transaction.

The repository row is held shared first, the order `apply` takes (it holds the row for
update), so a result waits for an apply in progress and then sees what it left: a leaf
deleted or replaced by it is no longer pending with this hash, and its late vector is
dropped. The leaf row is locked, so two deliveries of one result settle it once.

The stored chunk is built from the stored leaf (its file, lines and hash), never from the
message's metadata: only the vector and the model come from the indexer.
"""

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dal.retrieval import upsert_statement
from common_lib.domain import ChunkRef
from common_lib.merkle import LeafResult, Settled
from common_lib.models import CodeChunk, MerkleNode, Repository
from common_lib.vectors import VectorRecord


async def settle_leaf(session: AsyncSession, result: LeafResult) -> Settled:
    chunk = result.chunk
    repository = select(Repository.id).where(Repository.id == chunk.repository_id)
    if (await session.execute(repository.with_for_update(read=True))).one_or_none() is None:
        return "not_pending"
    leaf = (
        await session.execute(
            select(MerkleNode)
            .where(
                MerkleNode.repository_id == chunk.repository_id,
                MerkleNode.node_id == chunk.leaf_node_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        leaf is None
        or not leaf.is_leaf
        or leaf.hash != chunk.chunk_hash
        or leaf.parent_id != chunk.obfuscated_file_id
    ):
        return "not_pending"
    if leaf.index_state != "pending":
        return "already_settled"
    if result.embedded is not None:
        stored = VectorRecord(
            _stored_ref(chunk.repository_id, leaf), result.embedded.vector, result.embedded.model
        )
        await session.execute(upsert_statement([stored]))
    else:
        await session.execute(
            delete(CodeChunk).where(
                CodeChunk.repository_id == chunk.repository_id,
                CodeChunk.leaf_node_id == leaf.node_id,
            )
        )
    await session.execute(
        update(MerkleNode)
        .where(
            MerkleNode.repository_id == chunk.repository_id,
            MerkleNode.node_id == leaf.node_id,
        )
        .values(
            index_state="indexed" if result.embedded is not None else "rejected",
            rejection_reason=result.reason,
            pending_since=None,
            updated_at=func.now(),
        )
    )
    return "settled"


def _stored_ref(repository_id: uuid.UUID, leaf: MerkleNode) -> ChunkRef:
    """The leaf's parent is its file node, whose id is the file's obfuscated id."""
    assert leaf.parent_id is not None  # noqa: S101 - a leaf is never a top-level node
    assert leaf.line_start is not None  # noqa: S101 - CHECK: a leaf has lines
    assert leaf.line_end is not None  # noqa: S101 - CHECK: a leaf has lines
    return ChunkRef(
        repository_id, leaf.parent_id, leaf.line_start, leaf.line_end, leaf.hash, leaf.node_id
    )
