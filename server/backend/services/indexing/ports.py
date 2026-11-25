"""The Merkle tree store seam (ADR-0018). The comparison itself is the sync's job."""

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Protocol

from common_lib.domain import ChunkRef
from common_lib.merkle import (
    LeafResult,
    MerkleNodeView,
    MerkleUpdate,
    Rerequest,
    Settled,
    UploadStates,
)


class MerkleIndex(Protocol):
    async def root(self, repository_id: uuid.UUID) -> str | None:
        """The stored root hash, or None before the first sync."""
        ...

    async def children(
        self, repository_id: uuid.UUID, parent_ids: Collection[str | None]
    ) -> Mapping[str | None, Sequence[MerkleNodeView]]:
        """The stored children of each parent (None: the root), sorted by node id; an
        unknown parent has none. At most `MAX_NODES_PER_CALL` parents."""
        ...

    async def apply(
        self, repository_id: uuid.UUID, expected_root: str | None, update: MerkleUpdate
    ) -> None:
        """In one transaction, if the stored root is still `expected_root`: delete the
        subtrees of `update.deleted` with their chunks, store `update.nodes`, copy each
        leaf's line range to its chunk, and set the new root. A new leaf, or one whose
        hash changed, is stored `pending` and loses any old vector; a leaf with the same
        hash keeps its state. Afterwards every stored
        node must be reachable from the root. Raises `MerkleConflictError` when the root
        changed, and `InvalidValueError` for an unknown repository or a node left
        unreachable (an orphan or a cycle); either way nothing is written."""
        ...

    async def upload_states(
        self, repository_id: uuid.UUID, refs: Sequence[ChunkRef], *, window_s: int
    ) -> UploadStates:
        """Of the leaves `refs` name in `repository_id` with the ref's hash and file (its
        parent): the pending ones not published in the last `window_s` seconds, which an
        upload may hand to the indexer, and the rejected ones with their reasons. Another
        repository's refs never match. At most `MAX_CHUNKS_PER_UPLOAD` refs."""
        ...

    async def mark_published(self, repository_id: uuid.UUID, leaf_ids: Collection[str]) -> None:
        """The pending ones of `leaf_ids` were just handed to the indexer. At most
        `MAX_CHUNKS_PER_UPLOAD` ids."""
        ...

    async def rerequest(
        self, repository_id: uuid.UUID, *, window_s: int, max_attempts: int, limit: int
    ) -> Rerequest:
        """Asks again for at most `limit` pending leaves, the oldest first, that were
        neither asked for nor published in the last `window_s` seconds and were asked
        for fewer than `max_attempts` times: each counts one attempt more and is asked
        for now. A leaf another transaction holds is left for the next time."""
        ...

    async def settle(self, result: LeafResult) -> Settled:
        """In one transaction ordered against `apply`: if the leaf is still pending with
        the result's hash and file, store its vector (with the leaf's own metadata) and
        mark it `indexed`, or mark it `rejected` with the reason. A leaf settled before
        is `already_settled`; an unknown, deleted or replaced leaf, or one of another
        repository, is `not_pending` and nothing is written."""
        ...
