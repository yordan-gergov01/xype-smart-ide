"""The Merkle sync's messages on the `Session` stream (ADR-0018): protobuf to the sync's
values and back. Ids and hashes are checked here, at the edge; nothing from a report is
echoed in a refusal."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from xype.v1 import common_pb2, index_pb2, session_pb2

from backend.grpc.messages import response
from backend.services.indexing import SyncDelta, SyncDone, SyncRequest, SyncStep, TreeReport
from backend.services.session import MALFORMED, RefusedError
from common_lib.domain import ChunkRef, is_hmac_hex
from common_lib.errors import InvalidValueError
from common_lib.merkle import MAX_NODES_PER_CALL, MerkleNodeUpdate


def _repository(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise RefusedError(MALFORMED) from None


def root_report(message: index_pb2.MerkleRootReport) -> tuple[uuid.UUID, str]:
    if not is_hmac_hex(message.root_hash):
        raise RefusedError(MALFORMED)
    return _repository(message.repository_id), message.root_hash


def tree_report(message: index_pb2.MerkleTreeReport) -> TreeReport:
    if len(message.nodes) > MAX_NODES_PER_CALL:
        raise RefusedError(MALFORMED)
    try:
        nodes = tuple(
            MerkleNodeUpdate(
                node.node_id,
                node.parent_id or None,
                node.hash,
                node.is_leaf,
                (node.lines.start, node.lines.end) if node.is_leaf else None,
            )
            for node in message.nodes
        )
    except InvalidValueError:
        raise RefusedError(MALFORMED) from None
    return TreeReport(
        message.request_id,
        _repository(message.repository_id),
        message.root_hash,
        nodes,
        message.last_page,
    )


def _chunk(ref: ChunkRef) -> common_pb2.ChunkRef:
    return common_pb2.ChunkRef(
        repository_id=str(ref.repository_id),
        obfuscated_file_id=ref.obfuscated_file_id,
        lines=common_pb2.LineRange(start=ref.line_start, end=ref.line_end),
        chunk_hash=ref.chunk_hash,
        leaf_node_id=ref.leaf_node_id,
    )


def _body(step: SyncStep) -> session_pb2.SessionResponse:
    match step:
        case SyncRequest():
            request = index_pb2.MerkleSyncRequest(
                request_id=step.request_id,
                repository_id=str(step.repository_id),
                server_root_hash=step.server_root or "",
                node_ids=step.node_ids,
            )
            return session_pb2.SessionResponse(merkle_sync_request=request)
        case SyncDelta():
            delta = index_pb2.MerkleDelta(
                request_id=step.request_id,
                repository_id=str(step.repository_id),
                upload=[_chunk(ref) for ref in step.upload],
                deleted_node_ids=step.deleted,
            )
            return session_pb2.SessionResponse(merkle_delta=delta)
        case SyncDone():
            ack = index_pb2.IndexAck(request_id=step.request_id, server_root_hash=step.root)
            return session_pb2.SessionResponse(index_ack=ack)


def responses(
    now: datetime, correlation_id: str, steps: Sequence[SyncStep]
) -> list[session_pb2.SessionResponse]:
    return [response(now, correlation_id, _body(step)) for step in steps]
