"""`MerkleIndex` in Postgres (ADR-0018): `merkle_nodes` holds the tree, the root is
`repositories.merkle_root_hash`, and a leaf's chunk is the `code_chunks` row with
`leaf_node_id = node_id`.

`apply` is one transaction that first moves the root by compare-and-set: its row lock
also serialises two syncs of the same repository from different streams, and orders
every apply against `settle` (`backend.dal.indexing.settle`), which holds the same row
shared while it checks and settles one leaf.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import (
    CHAR,
    CTE,
    Integer,
    case,
    column,
    delete,
    func,
    literal,
    or_,
    select,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.dal.indexing.settle import settle_leaf
from backend.dal.transactions import transaction
from common_lib.domain import ChunkRef, is_hmac_hex
from common_lib.errors import InvalidValueError
from common_lib.merkle import (
    MAX_CHUNKS_PER_UPLOAD,
    MAX_NODES_PER_CALL,
    LeafResult,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
    Settled,
)
from common_lib.models import CodeChunk, MerkleNode, Repository

# Rows per statement: 1 000 nodes x 5 columns stays far below asyncpg's 32 767 parameters.
_BATCH = 1000


class PgMerkleIndex:
    def __init__(
        self,
        write_sessions: async_sessionmaker[AsyncSession],
        *,
        statement_timeout_ms: int = 60_000,
        lock_timeout_ms: int = 5_000,
    ) -> None:
        # The sync reads what it is about to write: the primary only.
        self._sessions = write_sessions
        # An apply holds the repository's row lock: a huge or stuck one must not hold it
        # without end, and a sync waiting for it gives up (`DependencyUnavailableError`).
        self._timeouts = {
            "statement_timeout": str(statement_timeout_ms),
            "lock_timeout": str(lock_timeout_ms),
        }

    async def root(self, repository_id: uuid.UUID) -> str | None:
        query = select(Repository.merkle_root_hash).where(Repository.id == repository_id)
        async with transaction(self._sessions) as session:
            root: str | None = (await session.execute(query)).scalar_one_or_none()
        return root

    async def children(
        self, repository_id: uuid.UUID, parent_ids: Collection[str | None]
    ) -> Mapping[str | None, Sequence[MerkleNodeView]]:
        if len(parent_ids) > MAX_NODES_PER_CALL:
            raise InvalidValueError(f"at most {MAX_NODES_PER_CALL} parents per call")
        if not all(parent is None or is_hmac_hex(parent) for parent in parent_ids):
            raise InvalidValueError("a parent id is 64-char lowercase hex")
        found: dict[str | None, list[MerkleNodeView]] = {parent: [] for parent in parent_ids}
        named = [parent for parent in parent_ids if parent is not None]
        if not found:
            return {}
        query = (
            select(MerkleNode.parent_id, MerkleNode.node_id, MerkleNode.hash, MerkleNode.is_leaf)
            .where(
                MerkleNode.repository_id == repository_id,
                or_(
                    MerkleNode.parent_id.in_(named),
                    MerkleNode.parent_id.is_(None) & literal(None in found),
                ),
            )
            .order_by(MerkleNode.node_id)
        )
        async with transaction(self._sessions) as session:
            for row in (await session.execute(query)).all():
                found[row.parent_id].append(MerkleNodeView(row.node_id, row.hash, row.is_leaf))
        return {parent: tuple(nodes) for parent, nodes in found.items()}

    async def apply(
        self, repository_id: uuid.UUID, expected_root: str | None, update: MerkleUpdate
    ) -> None:
        async with transaction(self._sessions) as session:
            await set_timeouts(session, self._timeouts)
            await _move_root(session, repository_id, expected_root, update.new_root)
            for start in range(0, len(update.deleted), _BATCH):
                await _delete_subtrees(
                    session, repository_id, update.deleted[start : start + _BATCH]
                )
            nodes = list({node.node_id: node for node in update.nodes}.values())
            for start in range(0, len(nodes), _BATCH):
                await _store(session, repository_id, nodes[start : start + _BATCH])
            await _drop_stale_vectors(session, repository_id, nodes)
            await _move_lines(session, repository_id, nodes)
            await _check_reachable(session, repository_id)

    async def pending(self, repository_id: uuid.UUID, refs: Sequence[ChunkRef]) -> frozenset[str]:
        if len(refs) > MAX_CHUNKS_PER_UPLOAD:
            raise InvalidValueError(f"at most {MAX_CHUNKS_PER_UPLOAD} chunks per upload")
        wanted = {
            (ref.leaf_node_id, ref.chunk_hash, ref.obfuscated_file_id)
            for ref in refs
            if ref.repository_id == repository_id
        }
        if not wanted:
            return frozenset()
        query = select(MerkleNode.node_id, MerkleNode.hash, MerkleNode.parent_id).where(
            MerkleNode.repository_id == repository_id,
            MerkleNode.node_id.in_([leaf_id for leaf_id, _, _ in wanted]),
            MerkleNode.is_leaf,
            MerkleNode.index_state == "pending",
        )
        async with transaction(self._sessions) as session:
            rows = (await session.execute(query)).all()
        return frozenset(row.node_id for row in rows if tuple(row) in wanted)

    async def settle(self, result: LeafResult) -> Settled:
        async with transaction(self._sessions) as session:
            await set_timeouts(session, self._timeouts)
            return await settle_leaf(session, result)


async def set_timeouts(session: AsyncSession, timeouts: Mapping[str, str]) -> None:
    for name, value in timeouts.items():
        # `SET LOCAL` takes no bind parameters; `set_config(..., true)` is the same.
        await session.execute(select(func.set_config(name, value, True)))


async def _move_root(
    session: AsyncSession, repository_id: uuid.UUID, expected: str | None, new: str
) -> None:
    """Locks the repository row first, so an unknown repository is told apart from a
    root that moved (the sync restarts only on the latter)."""
    locked = select(Repository.merkle_root_hash).where(Repository.id == repository_id)
    current = (await session.execute(locked.with_for_update())).one_or_none()
    if current is None:
        raise InvalidValueError("unknown repository")
    if current.merkle_root_hash != expected:
        raise MerkleConflictError("the root changed since the sync read it")
    await session.execute(
        update(Repository)
        .where(Repository.id == repository_id)
        .values(merkle_root_hash=new, last_synced_at=func.now())
    )


def _subtrees(repository_id: uuid.UUID, roots: Sequence[str]) -> CTE:
    """The deleted nodes and everything below them. `UNION` (not `UNION ALL`) ends even
    on a cyclic parent graph."""
    top = (
        select(MerkleNode.node_id, MerkleNode.is_leaf)
        .where(MerkleNode.repository_id == repository_id, MerkleNode.node_id.in_(list(roots)))
        .cte("subtree", recursive=True)
    )
    below = select(MerkleNode.node_id, MerkleNode.is_leaf).join(
        top, MerkleNode.parent_id == top.c.node_id
    )
    return top.union(below.where(MerkleNode.repository_id == repository_id))


async def _delete_subtrees(
    session: AsyncSession, repository_id: uuid.UUID, roots: Sequence[str]
) -> None:
    subtree = _subtrees(repository_id, roots)
    leaves = select(subtree.c.node_id).where(subtree.c.is_leaf)
    await session.execute(
        delete(CodeChunk).where(
            CodeChunk.repository_id == repository_id, CodeChunk.leaf_node_id.in_(leaves)
        )
    )
    await session.execute(
        delete(MerkleNode).where(
            MerkleNode.repository_id == repository_id,
            MerkleNode.node_id.in_(select(subtree.c.node_id)),
        )
    )


async def _check_reachable(session: AsyncSession, repository_id: uuid.UUID) -> None:
    """Every stored node hangs below the root: no orphan (its parent deleted or never
    stored) and no cycle. An unreachable node would never be deleted by a later sync,
    and a leaf's chunk would stay searchable. Refusing rolls the whole apply back."""
    top = (
        select(MerkleNode.node_id)
        .where(MerkleNode.repository_id == repository_id, MerkleNode.parent_id.is_(None))
        .cte("reachable", recursive=True)
    )
    below = (
        select(MerkleNode.node_id)
        .join(top, MerkleNode.parent_id == top.c.node_id)
        .where(MerkleNode.repository_id == repository_id)
    )
    reachable = top.union(below)
    counts = select(
        select(func.count()).select_from(reachable).scalar_subquery(),
        select(func.count())
        .select_from(MerkleNode)
        .where(MerkleNode.repository_id == repository_id)
        .scalar_subquery(),
    )
    reached, total = (await session.execute(counts)).one()
    if reached != total:
        raise InvalidValueError("a stored node is not reachable from the root")


async def _store(
    session: AsyncSession, repository_id: uuid.UUID, nodes: Sequence[MerkleNodeUpdate]
) -> None:
    """A new leaf, or one whose hash changed, is `pending` (its chunk must be embedded); a
    leaf whose hash is unchanged keeps its state, reason and re-request count. Inner
    nodes have no state."""
    rows: list[dict[str, Any]] = [  # reason: Any - column values
        {
            "repository_id": repository_id,
            "node_id": node.node_id,
            "parent_id": node.parent_id,
            "hash": node.hash,
            "is_leaf": node.is_leaf,
            "line_start": node.lines[0] if node.lines else None,
            "line_end": node.lines[1] if node.lines else None,
            "index_state": "pending" if node.is_leaf else None,
            "pending_since": func.now() if node.is_leaf else None,
        }
        for node in nodes
    ]
    statement = insert(MerkleNode).values(rows)
    new = statement.excluded
    unchanged = MerkleNode.is_leaf & new.is_leaf & (MerkleNode.hash == new.hash)

    def kept(column: Any, otherwise: Any) -> Any:  # reason: Any - SQL expressions
        return case((unchanged, column), else_=otherwise)

    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["repository_id", "node_id"],
            set_={
                "parent_id": new.parent_id,
                "hash": new.hash,
                "is_leaf": new.is_leaf,
                "line_start": new.line_start,
                "line_end": new.line_end,
                "index_state": kept(MerkleNode.index_state, new.index_state),
                "rejection_reason": kept(MerkleNode.rejection_reason, None),
                "pending_since": kept(MerkleNode.pending_since, new.pending_since),
                "request_attempts": kept(MerkleNode.request_attempts, 0),
                "updated_at": func.now(),
            },
        )
    )


async def _drop_stale_vectors(
    session: AsyncSession, repository_id: uuid.UUID, nodes: Sequence[MerkleNodeUpdate]
) -> None:
    """A stored node whose hash is no longer its chunk's content hash (a leaf whose
    content changed, or a node that is no longer a leaf) loses its vector: old content
    is never served under the new lines. The leaf is pending again."""
    pairs = [(node.node_id, node.hash) for node in nodes]
    for start in range(0, len(pairs), _BATCH):
        stored = values(column("node", CHAR(64)), column("hash", CHAR(64)), name="stored").data(
            pairs[start : start + _BATCH]
        )
        await session.execute(
            delete(CodeChunk).where(
                CodeChunk.repository_id == repository_id,
                CodeChunk.leaf_node_id == stored.c.node,
                CodeChunk.encrypted_chunk_hash != stored.c.hash,
            )
        )


async def _move_lines(
    session: AsyncSession, repository_id: uuid.UUID, leaves: Sequence[MerkleNodeUpdate]
) -> None:
    """A leaf whose content is unchanged but whose lines moved keeps its vector; only
    the stored range follows. A leaf without a chunk (rejected) changes nothing."""
    ranges = [(leaf.node_id, *leaf.lines) for leaf in leaves if leaf.lines is not None]
    for start in range(0, len(ranges), _BATCH):
        moved = values(
            column("leaf", CHAR(64)),
            column("first", Integer),
            column("last", Integer),
            name="moved",
        ).data(ranges[start : start + _BATCH])
        await session.execute(
            update(CodeChunk)
            .where(
                CodeChunk.repository_id == repository_id,
                CodeChunk.leaf_node_id == moved.c.leaf,
                (CodeChunk.line_start != moved.c.first) | (CodeChunk.line_end != moved.c.last),
            )
            .values(line_start=moved.c.first, line_end=moved.c.last, updated_at=func.now())
        )
