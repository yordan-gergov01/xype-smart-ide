"""The server side of the Merkle delta sync (ADR-0018) for one `Session` stream.

Level by level, the server asks for the children of the nodes whose hash differs from
its own. Each level is checked against the hash the client reported one level up (the
root first), so a skipped parent, a dropped page or a tree that changed mid-sync can
never be taken for deletions. When only leaves are left, the changes are applied at once
(`MerkleIndex.apply`, compare-and-set on the root) or, when chunks must be uploaded, sent
as a `SyncDelta` and held until they settle (slice 8). Ids and counts only are logged.

Every node a sync holds is counted against the stream's share and a `NodeBudget` that
all streams of the process share, and released when the sync ends or the stream closes.
"""

import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NoReturn

import structlog

from backend.services.indexing.ports import MerkleIndex
from backend.services.indexing.steps import (
    SYNC_MALFORMED,
    SYNC_MISMATCH,
    SYNC_REFUSED,
    SYNC_TOO_LARGE,
    SYNC_UNAVAILABLE,
    SYNC_UNKNOWN,
    SyncDelta,
    SyncDone,
    SyncPolicy,
    SyncRequest,
    SyncStep,
    TreeReport,
)
from backend.services.session import NO_ACCESS, RefusedError
from common_lib.clock import Clock
from common_lib.domain import ChunkRef, is_hmac_hex, uuid7
from common_lib.errors import DependencyUnavailableError, InvalidValueError
from common_lib.merkle import (
    MAX_NODES_PER_CALL,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
    inner_hash,
)

logger = structlog.get_logger(__name__)

# References per `SyncDelta` page: about 250 bytes each, well inside one message.
DELTA_PAGE = 2000


class NodeBudget:
    """Nodes held by every sync of one process (a few hundred bytes each)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def reserve(self, count: int) -> bool:
        if self.used + count > self.limit:
            return False
        self.used += count
        return True

    def release(self, count: int) -> None:
        if count > self.used:  # a double release would hide a leak
            raise InvalidValueError("released more nodes than were reserved")
        self.used -= count


@dataclass
class _Sync:
    repository_id: uuid.UUID
    expected_root: str | None  # the server's root when the sync started
    deadline: datetime
    restarts: int
    request_id: str = ""
    client_root: str | None = None
    held: int = 0  # nodes counted against the budgets
    # The level asked for: each parent with the hash its children must give (None: the
    # root, whose hash the report carries), and the children received so far.
    expected: dict[str | None, str | None] = field(default_factory=dict)
    received: dict[str | None, list[MerkleNodeUpdate]] = field(default_factory=dict)
    queue: deque[tuple[str, str]] = field(default_factory=deque)
    seen: set[str] = field(default_factory=set)
    stored: list[MerkleNodeUpdate] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    upload: list[ChunkRef] = field(default_factory=list)
    pending: MerkleUpdate | None = None  # waits for uploads (slice 8)


class MerkleSyncs:
    """The syncs of one stream, at most one per repository authorized at Hello."""

    def __init__(
        self,
        index: MerkleIndex,
        repositories: frozenset[uuid.UUID],
        *,
        policy: SyncPolicy,
        budget: NodeBudget,
        clock: Clock,
    ) -> None:
        self._index = index
        self._repositories = repositories
        self._policy = policy
        self._budget = budget
        self._clock = clock
        self._syncs: dict[uuid.UUID, _Sync] = {}

    def active(self) -> frozenset[uuid.UUID]:
        self._purge()
        return frozenset(self._syncs)

    def close(self) -> None:
        """The stream ended: every sync is forgotten and its nodes released."""
        for repository_id in list(self._syncs):
            self._drop(repository_id)

    async def start(
        self, repository_id: uuid.UUID, client_root: str | None, *, restarts: int = 0
    ) -> list[SyncStep]:
        """A root from Hello or a `MerkleRootReport` (equal: nothing to do), or None for
        the poll. A sync already running for the repository starts over."""
        if repository_id not in self._repositories:
            raise RefusedError(NO_ACCESS)
        server_root = await self._index.root(repository_id)
        self._drop(repository_id)
        if client_root is not None and client_root == server_root:
            return []
        deadline = self._clock.now() + timedelta(seconds=self._policy.deadline_s)
        sync = _Sync(repository_id, server_root, deadline, restarts)
        self._syncs[repository_id] = sync
        logger.info("sync.started", repository_id=str(repository_id), restarts=restarts)
        return [self._ask(sync, {None: None})]

    async def poll(self) -> list[SyncStep]:
        """The 10-minute check: every repository without a running sync."""
        steps: list[SyncStep] = []
        for repository_id in sorted(self._repositories - self.active()):
            steps += await self.start(repository_id, None)
        return steps

    async def report(self, report: TreeReport) -> list[SyncStep]:
        self._purge()
        sync = self._syncs.get(report.repository_id)
        if sync is None or sync.pending is not None or report.request_id != sync.request_id:
            raise RefusedError(SYNC_UNKNOWN)
        self._accept(sync, report)
        if not report.last_page:
            return []
        return await self._level_done(sync)

    def _ask(self, sync: _Sync, parents: dict[str | None, str | None]) -> SyncRequest:
        sync.request_id = str(uuid7(self._clock.now()))
        sync.expected, sync.received = parents, {parent: [] for parent in parents}
        node_ids = tuple(parent for parent in parents if parent is not None)
        return SyncRequest(sync.request_id, sync.repository_id, sync.expected_root, node_ids)

    def _accept(self, sync: _Sync, report: TreeReport) -> None:
        """Takes one page; anything malformed forgets the sync."""
        if None in sync.expected:
            if not is_hmac_hex(report.root_hash) or sync.client_root not in (
                None,
                report.root_hash,
            ):
                self._refuse(sync, SYNC_MALFORMED)
            sync.client_root = report.root_hash
        self._reserve(sync, len(report.nodes))
        for node in report.nodes:
            fits = node.parent_id in sync.expected and node.node_id not in sync.seen
            if not fits or (node.is_leaf and node.parent_id is None):
                self._refuse(sync, SYNC_MALFORMED)
            sync.seen.add(node.node_id)
            sync.received[node.parent_id].append(node)

    async def _level_done(self, sync: _Sync) -> list[SyncStep]:
        for parent, expected in sync.expected.items():
            want = sync.client_root if parent is None else expected
            if inner_hash(sync.received[parent]) != want:
                self._refuse(sync, SYNC_MISMATCH)
        if None in sync.expected and sync.client_root == sync.expected_root:
            self._drop(sync.repository_id)
            return []
        stored = await self._index.children(sync.repository_id, list(sync.expected))
        for parent, children in sync.received.items():
            gone = _compare(sync, parent, children, stored[parent])
            if gone is None:
                self._refuse(sync, SYNC_MALFORMED)
            else:
                self._reserve(sync, len(gone))
                sync.deleted.extend(gone)
        if sync.queue:
            count = min(len(sync.queue), MAX_NODES_PER_CALL)
            level = [sync.queue.popleft() for _ in range(count)]
            return [self._ask(sync, dict[str | None, str | None](level))]
        return await self._finish(sync)

    async def _finish(self, sync: _Sync) -> list[SyncStep]:
        assert sync.client_root is not None  # noqa: S101 - set by the root level
        try:
            update = MerkleUpdate(sync.client_root, tuple(sync.stored), tuple(sync.deleted))
        except InvalidValueError:  # a node both stored and deleted: it moved parents
            self._refuse(sync, SYNC_MALFORMED)
        if sync.upload:
            sync.pending = update
            return _deltas(sync, str(uuid7(self._clock.now())))
        try:
            await self._index.apply(sync.repository_id, sync.expected_root, update)
        except MerkleConflictError:
            self._drop(sync.repository_id)
            if sync.restarts >= self._policy.max_restarts:
                logger.warning("sync.gave_up", repository_id=str(sync.repository_id))
                return []
            return await self.start(sync.repository_id, None, restarts=sync.restarts + 1)
        except InvalidValueError:
            self._refuse(sync, SYNC_REFUSED)
        except DependencyUnavailableError:
            # A timeout or a lock held by another sync: this sync ends, the stream stays.
            self._refuse(sync, SYNC_UNAVAILABLE)
        self._drop(sync.repository_id)
        logger.info(
            "sync.applied",
            repository_id=str(sync.repository_id),
            stored=len(update.nodes),
            deleted=len(update.deleted),
        )
        return [SyncDone(sync.request_id, sync.repository_id, sync.client_root)]

    def _reserve(self, sync: _Sync, count: int) -> None:
        held = sum(s.held for s in self._syncs.values())
        if held + count > self._policy.max_nodes_per_stream or not self._budget.reserve(count):
            self._refuse(sync, SYNC_TOO_LARGE)
        sync.held += count

    def _drop(self, repository_id: uuid.UUID) -> None:
        sync = self._syncs.pop(repository_id, None)
        if sync is not None:
            self._budget.release(sync.held)

    def _refuse(self, sync: _Sync, reason: str) -> NoReturn:
        self._drop(sync.repository_id)
        logger.warning("sync.refused", repository_id=str(sync.repository_id), reason=reason)
        raise RefusedError(reason)

    def _purge(self) -> None:
        now = self._clock.now()
        for repository_id in [r for r, s in self._syncs.items() if now >= s.deadline]:
            self._drop(repository_id)
            logger.info("sync.expired", repository_id=str(repository_id))


def _compare(
    sync: _Sync,
    parent: str | None,
    children: list[MerkleNodeUpdate],
    stored: Sequence[MerkleNodeView],
) -> list[str] | None:
    """Keeps changed inner nodes (and asks for their children next), keeps every leaf
    (its lines may have moved) and uploads changed or new leaves. Returns the stored
    children the client no longer has, or None when a node changed kind (leaf and inner
    ids come from different HMAC domains, so an honest client never does that)."""
    remaining = {node.node_id: node for node in stored}
    for node in children:
        old = remaining.pop(node.node_id, None)
        if old is not None and old.is_leaf != node.is_leaf:
            return None
        changed = old is None or old.hash != node.hash
        if node.is_leaf:
            sync.stored.append(node)
            if changed and node.lines is not None and parent is not None:
                sync.upload.append(_chunk(sync.repository_id, parent, node))
        elif changed:
            sync.stored.append(node)
            sync.queue.append((node.node_id, node.hash))
    return list(remaining)


def _chunk(repository_id: uuid.UUID, file_id: str, leaf: MerkleNodeUpdate) -> ChunkRef:
    """A leaf's parent is its file node, whose id is the file's obfuscated id (ADR-0016)."""
    assert leaf.lines is not None  # noqa: S101 - leaves always carry lines
    return ChunkRef(repository_id, file_id, leaf.lines[0], leaf.lines[1], leaf.hash, leaf.node_id)


def _deltas(sync: _Sync, request_id: str) -> list[SyncStep]:
    """The uploads and deletions in pages; deletions ride on the first pages."""
    pages = max(1, -(-max(len(sync.upload), len(sync.deleted)) // DELTA_PAGE))
    return [
        SyncDelta(
            request_id,
            sync.repository_id,
            tuple(sync.upload[n * DELTA_PAGE : (n + 1) * DELTA_PAGE]),
            tuple(sync.deleted[n * DELTA_PAGE : (n + 1) * DELTA_PAGE]),
        )
        for n in range(pages)
    ]
