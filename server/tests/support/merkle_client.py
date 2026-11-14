"""A client's Merkle tree for the sync tests: built from a nested spec with the real
inner hash (ADR-0018), and answering `SyncRequest`s like the extension (slice 11).

A spec maps a node id to either a nested spec (a directory) or a list of leaves
`(leaf_id, content_hash, (line_start, line_end))` (a file)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from backend.services.indexing import MerkleSyncs, SyncRequest, SyncStep, TreeReport
from common_lib.merkle import MerkleNodeUpdate, MerkleUpdate, inner_hash

type Leaf = tuple[str, str, tuple[int, int]]
type Spec = dict[str, "Spec | list[Leaf]"]


def build(spec: Spec, parent: str | None = None) -> list[MerkleNodeUpdate]:
    nodes: list[MerkleNodeUpdate] = []
    for node_id, body in spec.items():
        if isinstance(body, list):
            children = [MerkleNodeUpdate(lid, node_id, h, True, lines) for lid, h, lines in body]
            below = children
        else:
            below = build(body, node_id)
            children = [n for n in below if n.parent_id == node_id]
        nodes += [MerkleNodeUpdate(node_id, parent, inner_hash(children), False), *below]
    return nodes


@dataclass
class ClientTree:
    spec: Spec

    @property
    def nodes(self) -> list[MerkleNodeUpdate]:
        return build(self.spec)

    @property
    def root(self) -> str:
        return inner_hash(n for n in self.nodes if n.parent_id is None)

    def as_update(self) -> MerkleUpdate:
        """The whole tree, as an index that has seen every upload settle stores it."""
        return MerkleUpdate(self.root, tuple(self.nodes))

    def answer(self, request: SyncRequest, page: int = 2000) -> list[TreeReport]:
        parents = set(request.node_ids) if request.node_ids else {None}
        children = [n for n in self.nodes if n.parent_id in parents]
        pages = [children[i : i + page] for i in range(0, len(children), page)] or [[]]
        return [
            TreeReport(
                request.request_id,
                request.repository_id,
                self.root,
                tuple(chunk),
                last_page=n == len(pages) - 1,
            )
            for n, chunk in enumerate(pages)
        ]


async def drive(
    syncs: MerkleSyncs, tree: ClientTree, steps: Sequence[SyncStep]
) -> tuple[list[SyncStep], int]:
    """Answers every request until the sync ends; returns its last steps and the number
    of requests the server sent."""
    requests, current = 0, list(steps)
    while current and isinstance(current[0], SyncRequest):
        requests += 1
        replies: list[SyncStep] = []
        for report in tree.answer(current[0]):
            replies += await syncs.report(report)
        current = replies
    return current, requests


def repository() -> uuid.UUID:
    return uuid.uuid4()
