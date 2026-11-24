"""`FakeMerkleIndex`: the tree, its leaves' states and their vectors in memory, with the
checks `PgMerkleIndex` makes (support/contracts/merkle.py)."""

import dataclasses
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence

from common_lib.domain import ChunkRef, is_hmac_hex
from common_lib.errors import InvalidValueError
from common_lib.merkle import (
    MAX_NODES_PER_CALL,
    LeafResult,
    LeafState,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
    RejectionReason,
    Settled,
)
from common_lib.vectors import VectorRecord

type State = tuple[LeafState, RejectionReason | None]


class FakeMerkleIndex:
    def __init__(self) -> None:
        self.roots: dict[uuid.UUID, str | None] = {}
        self.nodes: dict[uuid.UUID, dict[str, MerkleNodeUpdate]] = {}
        self.states: dict[uuid.UUID, dict[str, State]] = {}
        self.vectors: dict[uuid.UUID, dict[str, VectorRecord]] = {}

    def register(self, repository_id: uuid.UUID) -> None:
        self.roots[repository_id] = None
        self.nodes[repository_id] = {}
        self.states[repository_id] = {}
        self.vectors[repository_id] = {}

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
        for node in update.nodes:
            _store(tree.get(node.node_id), node, states, vectors)
            tree[node.node_id] = node
        if _subtrees(tree, [n.node_id for n in tree.values() if n.parent_id is None]) != set(tree):
            raise InvalidValueError("a stored node is not reachable from the root")
        self.nodes[repository_id] = tree
        self.states[repository_id], self.vectors[repository_id] = states, vectors
        self.roots[repository_id] = update.new_root

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
        return "settled"


def _store(
    old: MerkleNodeUpdate | None,
    node: MerkleNodeUpdate,
    states: dict[str, State],
    vectors: dict[str, VectorRecord],
) -> None:
    """As `PgMerkleIndex` stores a node: an unchanged leaf keeps its state (and its
    vector follows its lines), anything else is pending or has no state."""
    unchanged = old is not None and old.is_leaf and node.is_leaf and old.hash == node.hash
    if unchanged and node.lines is not None:
        if node.node_id in vectors:
            moved = dataclasses.replace(
                vectors[node.node_id].chunk, line_start=node.lines[0], line_end=node.lines[1]
            )
            vectors[node.node_id] = dataclasses.replace(vectors[node.node_id], chunk=moved)
        return
    vectors.pop(node.node_id, None)
    if node.is_leaf:
        states[node.node_id] = ("pending", None)
    else:
        states.pop(node.node_id, None)


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
