"""What a Merkle sync exchanges with a client (ADR-0018), without the transport: the
client's reported tree level in, and the server's next step out."""

import uuid
from dataclasses import dataclass

from common_lib.domain import ChunkRef
from common_lib.merkle import MAX_NODES_PER_APPLY, MerkleNodeUpdate

# Fixed refusal reasons, safe to send back (the stream answers them in a StreamError).
SYNC_UNKNOWN = "no sync in progress"
SYNC_MISMATCH = "tree does not match its hashes"
SYNC_MALFORMED = "malformed tree report"
SYNC_TOO_LARGE = "sync too large"
SYNC_REFUSED = "tree refused"
SYNC_UNAVAILABLE = "sync could not be applied now"


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    # A sync (descent, and the wait for uploads) is forgotten after this long.
    deadline_s: int
    # Restarts from the root after another sync of the repository applied first.
    max_restarts: int
    # Nodes one stream holds for its syncs at once; all streams share a `NodeBudget` too.
    max_nodes_per_stream: int = MAX_NODES_PER_APPLY


@dataclass(frozen=True, slots=True)
class TreeReport:
    """One page of the client's answer to a `SyncRequest`. Children of the root carry
    `parent_id` None; `root_hash` is the client's root."""

    request_id: str
    repository_id: uuid.UUID
    root_hash: str
    nodes: tuple[MerkleNodeUpdate, ...]
    last_page: bool


@dataclass(frozen=True, slots=True)
class SyncRequest:
    """Asks for the children of `node_ids`; empty asks for the root's children."""

    request_id: str
    repository_id: uuid.UUID
    server_root: str | None
    node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyncDelta:
    """The chunks to upload and the nodes the server dropped; one page of them."""

    request_id: str
    repository_id: uuid.UUID
    upload: tuple[ChunkRef, ...]
    deleted: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyncDone:
    """The sync applied: the server's root is now `root`."""

    request_id: str
    repository_id: uuid.UUID
    root: str


type SyncStep = SyncRequest | SyncDelta | SyncDone
