"""The server's view of a repository's Merkle tree (ADR-0018), as the sync and the
`MerkleIndex` store share it. Only HMAC ids, hashes and line numbers: never a path.

The root itself is `repositories.merkle_root_hash`; its children have no parent
(`parent_id` None here, NULL in `merkle_nodes`, the empty string on the wire).
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from common_lib.domain import ChunkRef, is_hmac_hex
from common_lib.errors import InvalidValueError, XypeError
from common_lib.vectors import VectorRecord

# ADR-0018: at most 2 000 nodes per report, so at most that many parents per lookup.
MAX_NODES_PER_CALL = 2000
# One `ChunkUpload` carries at most this many chunks (ADR-0018).
MAX_CHUNKS_PER_UPLOAD = 256
# One apply carries a whole sync, the first one a whole repository: about 300 000 chunks
# at the hosted target (scale-estimates), plus their files and directories.
MAX_NODES_PER_APPLY = 500_000
# Line numbers are 4-byte integers in the inner hash and on the wire, and `int4` in
# `code_chunks`, so at most 2^31 - 1.
_MAX_LINE = 2**31 - 1


# A stored leaf (ADR-0018, slice 8c2): `pending` until the indexer's result settles it,
# then `indexed` (its vector is stored) or `rejected` (with a fixed reason, no vector).
type LeafState = Literal["pending", "indexed", "rejected"]
LEAF_STATES: tuple[LeafState, ...] = ("pending", "indexed", "rejected")
# Why the indexer did not embed a chunk (ADR-0019): `unscannable`, the server check
# could not run in time or on text that is not UTF-8; `unembeddable`, the model refused.
type RejectionReason = Literal["secret_found", "too_large", "unscannable", "unembeddable"]
REJECTION_REASONS: tuple[RejectionReason, ...] = (
    "secret_found",
    "too_large",
    "unscannable",
    "unembeddable",
)
# What settling a result did: `settled` it, found it `already_settled` (a repeated
# delivery: acknowledged), or found no pending leaf with that hash (`not_pending`: a late
# result for deleted or replaced content, or one for another repository).
type Settled = Literal["settled", "already_settled", "not_pending"]


class MerkleError(XypeError):
    """Root of the Merkle tree's errors."""


class MerkleConflictError(MerkleError):
    """The stored root changed since the sync read it: another sync applied first. The
    caller starts again from the root."""


def _check_hex(*values: str | None) -> None:
    if not all(value is None or is_hmac_hex(value) for value in values):
        raise InvalidValueError("Merkle ids and hashes are 64-char lowercase hex")


@dataclass(frozen=True, slots=True)
class MerkleNodeView:
    """A stored node as the sync compares it."""

    node_id: str
    hash: str
    is_leaf: bool

    def __post_init__(self) -> None:
        _check_hex(self.node_id, self.hash)


@dataclass(frozen=True, slots=True)
class MerkleNodeUpdate:
    """A node to store. A leaf carries its line range, which `apply` copies to its
    stored chunk (a moved chunk is not re-embedded); inner nodes carry none."""

    node_id: str
    parent_id: str | None
    hash: str
    is_leaf: bool
    lines: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        _check_hex(self.node_id, self.parent_id, self.hash)
        if self.node_id == self.parent_id:
            raise InvalidValueError("a node cannot be its own parent")
        if self.is_leaf != (self.lines is not None):
            raise InvalidValueError("a leaf has a line range and an inner node has none")
        if self.lines is not None and not 1 <= self.lines[0] <= self.lines[1] <= _MAX_LINE:
            raise InvalidValueError("invalid line range")


@dataclass(frozen=True, slots=True)
class MerkleUpdate:
    """One sync's result: the new root, the nodes to store and the nodes whose subtrees
    (and their chunks) go."""

    new_root: str
    nodes: Sequence[MerkleNodeUpdate]
    deleted: Sequence[str] = ()

    def __post_init__(self) -> None:
        if max(len(self.nodes), len(self.deleted)) > MAX_NODES_PER_APPLY:
            raise InvalidValueError(f"at most {MAX_NODES_PER_APPLY} nodes per apply")
        _check_hex(self.new_root, *self.deleted)
        if {n.node_id for n in self.nodes} & set(self.deleted):
            raise InvalidValueError("a node cannot be both stored and deleted")


def inner_hash(children: Iterable[MerkleNodeUpdate]) -> str:
    """The hash of an inner node (or of the root) over its children (ADR-0018): SHA-256
    over the children sorted by node id, each as `node_id` and `hash` (64 lowercase hex
    characters each, ASCII), one byte `is_leaf` (1 or 0) and, for a leaf, `line_start` and
    `line_end` as 4-byte big-endian integers. No children: the hash of nothing."""
    digest = hashlib.sha256()
    for child in sorted(children, key=lambda c: c.node_id):
        digest.update(child.node_id.encode("ascii") + child.hash.encode("ascii"))
        digest.update(b"\x01" if child.is_leaf else b"\x00")
        if child.lines is not None:
            digest.update(child.lines[0].to_bytes(4, "big") + child.lines[1].to_bytes(4, "big"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class LeafResult:
    """The indexer's result for one leaf: its vector, or the reason it has none. The
    store checks `chunk` against the pending leaf and writes the leaf's own metadata."""

    chunk: ChunkRef
    embedded: VectorRecord | None = None
    reason: RejectionReason | None = None

    def __post_init__(self) -> None:
        if (self.embedded is None) == (self.reason is None):
            raise InvalidValueError("a result has a vector or a rejection reason")
        if self.embedded is not None and self.embedded.chunk != self.chunk:
            raise InvalidValueError("a vector belongs to its own chunk")


@dataclass(frozen=True, slots=True)
class UploadStates:
    """What the store holds for the leaves an upload names (slice 8c3c): `pending`, the
    ones to publish (pending with the ref's hash and file, and not published within
    the settle window); `rejected`, the ones settled as rejected, with their reasons."""

    pending: frozenset[str] = frozenset()
    rejected: Mapping[str, RejectionReason] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Rerequest:
    """Stale pending leaves asked for again (slice 8c3c): their refs, and how many of
    them were asked for the last time (they stay pending afterwards)."""

    refs: tuple[ChunkRef, ...] = ()
    last: int = 0
