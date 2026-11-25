"""`FakeMerkleIndex`: the tree, its leaves' states and their vectors in memory, with the
checks `PgMerkleIndex` makes (support/contracts/merkle.py). Times come from its clock,
as the database's `now()` stamps them in Postgres."""

import dataclasses
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from common_lib.clock import Clock, SystemClock
from common_lib.domain import ChunkRef, is_hmac_hex
from common_lib.errors import InvalidValueError
from common_lib.merkle import (
    MAX_CHUNKS_PER_UPLOAD,
    MAX_NODES_PER_CALL,
    LeafResult,
    LeafState,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
    RejectionReason,
    Rerequest,
    Settled,
    UploadStates,
)
from common_lib.vectors import VectorRecord

type State = tuple[LeafState, RejectionReason | None]


class _DatabaseClock:
    """The system clock, strictly rising like `now()` across transactions (the system
    clock may repeat a value within its resolution)."""

    def __init__(self) -> None:
        self._system, self._last = SystemClock(), datetime.min.replace(tzinfo=UTC)

    def now(self) -> datetime:
        self._last = max(self._system.now(), self._last + timedelta(microseconds=1))
        return self._last


@dataclass
class Ask:
    """A pending leaf's `pending_since`, `request_attempts` and `published_at`."""

    since: datetime
    attempts: int = 0
    published: datetime | None = None


class FakeMerkleIndex:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or _DatabaseClock()
        self.asks: dict[uuid.UUID, dict[str, Ask]] = {}
        self.roots: dict[uuid.UUID, str | None] = {}
        self.nodes: dict[uuid.UUID, dict[str, MerkleNodeUpdate]] = {}
        self.states: dict[uuid.UUID, dict[str, State]] = {}
        self.vectors: dict[uuid.UUID, dict[str, VectorRecord]] = {}

    def register(self, repository_id: uuid.UUID) -> None:
        self.roots[repository_id] = None
        self.nodes[repository_id] = {}
        self.states[repository_id] = {}
        self.vectors[repository_id] = {}
        self.asks[repository_id] = {}

    async def state(self, repository_id: uuid.UUID, leaf_id: str) -> State | None:
        return self.states.get(repository_id, {}).get(leaf_id)

    async def vector(self, repository_id: uuid.UUID, leaf_id: str) -> VectorRecord | None:
        return self.vectors.get(repository_id, {}).get(leaf_id)

    async def root(self, repository_id: uuid.UUID) -> str | None:
        return self.roots.get(repository_id)

    async def children(
        self, repository_id: uuid.UUID, parent_ids: Collection[str | None]
    ) -> Mapping[str | None, Sequence[MerkleNodeView]]:
        if len(parent_ids) > MAX_NODES_PER_CALL:
            raise InvalidValueError(f"at most {MAX_NODES_PER_CALL} parents per call")
        if not all(parent is None or is_hmac_hex(parent) for parent in parent_ids):
            raise InvalidValueError("a parent id is 64-char lowercase hex")
        tree = self.nodes.get(repository_id, {})
        return {
            parent: tuple(
                MerkleNodeView(n.node_id, n.hash, n.is_leaf)
                for n in sorted(tree.values(), key=lambda n: n.node_id)
                if n.parent_id == parent
            )
            for parent in parent_ids
        }

    async def apply(
        self, repository_id: uuid.UUID, expected_root: str | None, update: MerkleUpdate
    ) -> None:
        if repository_id not in self.roots:
            raise InvalidValueError("unknown repository")
        if self.roots[repository_id] != expected_root:
            raise MerkleConflictError("the root changed since the sync read it")
        tree = dict(self.nodes[repository_id])
        states, vectors = dict(self.states[repository_id]), dict(self.vectors[repository_id])
        for node_id in _subtrees(tree, update.deleted):
            del tree[node_id]
            states.pop(node_id, None)
            vectors.pop(node_id, None)
        repended = set()
        for node in update.nodes:
            if _store(tree.get(node.node_id), node, states, vectors):
                repended.add(node.node_id)
            tree[node.node_id] = node
        if _subtrees(tree, [n.node_id for n in tree.values() if n.parent_id is None]) != set(tree):
            raise InvalidValueError("a stored node is not reachable from the root")
        self.nodes[repository_id] = tree
        self.states[repository_id], self.vectors[repository_id] = states, vectors
        old, now = self.asks[repository_id], self.clock.now()
        self.asks[repository_id] = {
            leaf_id: old[leaf_id] if leaf_id in old and leaf_id not in repended else Ask(now)
            for leaf_id, (state, _) in states.items()
            if state == "pending"
        }
        self.roots[repository_id] = update.new_root

    async def upload_states(
        self, repository_id: uuid.UUID, refs: Sequence[ChunkRef], *, window_s: int
    ) -> UploadStates:
        if len(refs) > MAX_CHUNKS_PER_UPLOAD:
            raise InvalidValueError(f"at most {MAX_CHUNKS_PER_UPLOAD} chunks per upload")
        tree, states = self.nodes.get(repository_id, {}), self.states.get(repository_id, {})
        cutoff = self.clock.now() - timedelta(seconds=window_s)
        pending, rejected = set(), {}
        for ref in refs:
            leaf = tree.get(ref.leaf_node_id)
            if (
                ref.repository_id != repository_id
                or leaf is None
                or not leaf.is_leaf
                or (leaf.hash, leaf.parent_id) != (ref.chunk_hash, ref.obfuscated_file_id)
            ):
                continue
            state, reason = states[leaf.node_id]
            if state == "pending" and _quiet(self.asks[repository_id][leaf.node_id], cutoff):
                pending.add(leaf.node_id)
            elif state == "rejected" and reason is not None:
                rejected[leaf.node_id] = reason
        return UploadStates(frozenset(pending), rejected)

    async def mark_published(self, repository_id: uuid.UUID, leaf_ids: Collection[str]) -> None:
        if len(leaf_ids) > MAX_CHUNKS_PER_UPLOAD:
            raise InvalidValueError(f"at most {MAX_CHUNKS_PER_UPLOAD} chunks per upload")
        asks = self.asks.get(repository_id, {})
        for leaf_id in leaf_ids:
            if leaf_id in asks:
                asks[leaf_id].published = self.clock.now()

    async def rerequest(
        self, repository_id: uuid.UUID, *, window_s: int, max_attempts: int, limit: int
    ) -> Rerequest:
        now = self.clock.now()
        cutoff = now - timedelta(seconds=window_s)
        asks, tree = self.asks.get(repository_id, {}), self.nodes.get(repository_id, {})
        stale = sorted(
            (ask.since, leaf_id)
            for leaf_id, ask in asks.items()
            if ask.since < cutoff and _quiet(ask, cutoff) and ask.attempts < max_attempts
        )[:limit]
        refs = []
        for _, leaf_id in sorted(stale, key=lambda pair: pair[1]):
            asks[leaf_id] = Ask(now, asks[leaf_id].attempts + 1)
            leaf = tree[leaf_id]
            assert leaf.lines is not None
            assert leaf.parent_id is not None
            refs.append(ChunkRef(repository_id, leaf.parent_id, *leaf.lines, leaf.hash, leaf_id))
        last = sum(1 for ref in refs if asks[ref.leaf_node_id].attempts >= max_attempts)
        return Rerequest(tuple(refs), last)

    async def settle(self, result: LeafResult) -> Settled:
        chunk = result.chunk
        leaf = self.nodes.get(chunk.repository_id, {}).get(chunk.leaf_node_id)
        if (
            leaf is None
            or not leaf.is_leaf
            or leaf.hash != chunk.chunk_hash
            or leaf.parent_id != chunk.obfuscated_file_id
            or leaf.lines is None
        ):
            return "not_pending"
        states = self.states[chunk.repository_id]
        if states[leaf.node_id][0] != "pending":
            return "already_settled"
        vectors = self.vectors[chunk.repository_id]
        if result.embedded is not None:
            first, last = leaf.lines
            ref = ChunkRef(
                chunk.repository_id, chunk.obfuscated_file_id, first, last, leaf.hash, leaf.node_id
            )
            vectors[leaf.node_id] = dataclasses.replace(result.embedded, chunk=ref)
            states[leaf.node_id] = ("indexed", None)
        else:
            vectors.pop(leaf.node_id, None)
            states[leaf.node_id] = ("rejected", result.reason)
        self.asks[chunk.repository_id].pop(leaf.node_id, None)
        return "settled"


def _store(
    old: MerkleNodeUpdate | None,
    node: MerkleNodeUpdate,
    states: dict[str, State],
    vectors: dict[str, VectorRecord],
) -> bool:
    """As `PgMerkleIndex` stores a node: an unchanged leaf keeps its state (and its
    vector follows its lines), anything else is pending or has no state. True when
    the node is a leaf pending anew."""
    unchanged = old is not None and old.is_leaf and node.is_leaf and old.hash == node.hash
    if unchanged and node.lines is not None:
        if node.node_id in vectors:
            moved = dataclasses.replace(
                vectors[node.node_id].chunk, line_start=node.lines[0], line_end=node.lines[1]
            )
            vectors[node.node_id] = dataclasses.replace(vectors[node.node_id], chunk=moved)
        return False
    vectors.pop(node.node_id, None)
    if node.is_leaf:
        states[node.node_id] = ("pending", None)
        return True
    states.pop(node.node_id, None)
    return False


def _quiet(ask: Ask, cutoff: datetime) -> bool:
    return ask.published is None or ask.published < cutoff


def _subtrees(tree: Mapping[str, MerkleNodeUpdate], roots: Iterable[str]) -> set[str]:
    gone: set[str] = set()
    todo = [node_id for node_id in roots if node_id in tree]
    while todo:
        node_id = todo.pop()
        if node_id in gone:
            continue  # a cycle ends here, as `UNION` ends it in Postgres
        gone.add(node_id)
        todo.extend(n.node_id for n in tree.values() if n.parent_id == node_id)
    return gone
