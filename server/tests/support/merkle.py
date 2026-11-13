"""`FakeMerkleIndex`: the tree in memory, with the checks `PgMerkleIndex` makes
(support/contracts/merkle.py). Chunks and their line ranges are the adapter's
integration tests' business."""

import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence

from common_lib.domain import is_hmac_hex
from common_lib.errors import InvalidValueError
from common_lib.merkle import (
    MAX_NODES_PER_CALL,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
)


class FakeMerkleIndex:
    def __init__(self) -> None:
        self.roots: dict[uuid.UUID, str | None] = {}
        self.nodes: dict[uuid.UUID, dict[str, MerkleNodeUpdate]] = {}

    def register(self, repository_id: uuid.UUID) -> None:
        self.roots[repository_id] = None
        self.nodes[repository_id] = {}

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
        for node_id in _subtrees(tree, update.deleted):
            del tree[node_id]
        for node in update.nodes:
            tree[node.node_id] = node
        if _subtrees(tree, [n.node_id for n in tree.values() if n.parent_id is None]) != set(tree):
            raise InvalidValueError("a stored node is not reachable from the root")
        self.nodes[repository_id] = tree
        self.roots[repository_id] = update.new_root


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
