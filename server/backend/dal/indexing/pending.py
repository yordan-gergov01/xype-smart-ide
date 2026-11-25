"""Pending leaves between the sync and the indexer (ADR-0018 amendment, slice 8c3c),
inside the caller's transaction. Times are the database's (`now()`), the clock
`apply` stamps `pending_since` with.

- `upload_states`: of the leaves an upload names, the pending ones not published within
  the settle window (to publish) and the rejected ones (reported in `IndexAck`).
- `mark_published`: the leaves just handed to the indexer.
- `rerequest`: the oldest pending leaves not asked for nor published within the window
  and with attempts left; each is asked for again (`pending_since` now, one attempt
  more). Rows another transaction holds are skipped, so two streams never ask for one
  leaf twice and an apply in progress is never waited for.
"""

import uuid
from collections.abc import Collection, Sequence
from datetime import timedelta
from typing import cast

from sqlalchemy import CTE, ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common_lib.domain import ChunkRef
from common_lib.merkle import RejectionReason, Rerequest, UploadStates
from common_lib.models import MerkleNode


def _quiet_since(window_s: int) -> ColumnElement[bool]:
    """Neither asked for nor published after `now() - window`."""
    cutoff = func.now() - timedelta(seconds=window_s)
    return or_(MerkleNode.published_at.is_(None), MerkleNode.published_at < cutoff)


async def upload_states(
    session: AsyncSession, repository_id: uuid.UUID, refs: Sequence[ChunkRef], window_s: int
) -> UploadStates:
    wanted = {
        (ref.leaf_node_id, ref.chunk_hash, ref.obfuscated_file_id)
        for ref in refs
        if ref.repository_id == repository_id
    }
    if not wanted:
        return UploadStates()
    query = select(
        MerkleNode.node_id,
        MerkleNode.hash,
        MerkleNode.parent_id,
        MerkleNode.index_state,
        MerkleNode.rejection_reason,
        _quiet_since(window_s).label("quiet"),
    ).where(
        MerkleNode.repository_id == repository_id,
        MerkleNode.node_id.in_([leaf_id for leaf_id, _, _ in wanted]),
        MerkleNode.is_leaf,
    )
    rows = [
        row
        for row in (await session.execute(query)).all()
        if (row.node_id, row.hash, row.parent_id) in wanted
    ]
    return UploadStates(
        frozenset(r.node_id for r in rows if r.index_state == "pending" and r.quiet),
        {
            r.node_id: cast("RejectionReason", r.rejection_reason)
            for r in rows
            if r.index_state == "rejected"
        },
    )


async def mark_published(
    session: AsyncSession, repository_id: uuid.UUID, leaf_ids: Collection[str]
) -> None:
    await session.execute(
        update(MerkleNode)
        .where(
            MerkleNode.repository_id == repository_id,
            MerkleNode.node_id.in_(list(leaf_ids)),
            MerkleNode.index_state == "pending",
        )
        .values(published_at=func.now())
    )


def _stale(repository_id: uuid.UUID, window_s: int, max_attempts: int, limit: int) -> CTE:
    """The leaves to ask for again, locked; a CTE runs once (as an `IN` subquery the
    planner may rescan it per row, and each rescan can lock other rows), and
    `MATERIALIZED` keeps the planner from folding it in."""
    return (
        select(MerkleNode.node_id)
        .where(
            MerkleNode.repository_id == repository_id,
            MerkleNode.index_state == "pending",
            MerkleNode.pending_since < func.now() - timedelta(seconds=window_s),
            _quiet_since(window_s),
            MerkleNode.request_attempts < max_attempts,
        )
        .order_by(MerkleNode.pending_since, MerkleNode.node_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("stale")
        .prefix_with("MATERIALIZED")
    )


async def rerequest(
    session: AsyncSession,
    repository_id: uuid.UUID,
    *,
    window_s: int,
    max_attempts: int,
    limit: int,
) -> Rerequest:
    stale = _stale(repository_id, window_s, max_attempts, limit)
    asked = (
        update(MerkleNode)
        .where(
            MerkleNode.repository_id == repository_id,
            MerkleNode.node_id.in_(select(stale.c.node_id)),
        )
        .values(
            pending_since=func.now(),
            request_attempts=MerkleNode.request_attempts + 1,
            published_at=None,
            updated_at=func.now(),
        )
        .returning(
            MerkleNode.node_id,
            MerkleNode.parent_id,
            MerkleNode.line_start,
            MerkleNode.line_end,
            MerkleNode.hash,
            MerkleNode.request_attempts,
        )
    )
    rows = sorted((await session.execute(asked)).all(), key=lambda row: row.node_id)
    refs = tuple(
        ChunkRef(repository_id, r.parent_id, r.line_start, r.line_end, r.hash, r.node_id)
        for r in rows
    )
    return Rerequest(refs, sum(1 for r in rows if r.request_attempts >= max_attempts))
