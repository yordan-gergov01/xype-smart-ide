"""`ChunkUpload` on the `Session` stream (ADR-0015, slice 8c3b): protobuf to an `Upload`
and its `IndexAck` back. Ids and hashes are checked here, at the edge; the ciphertext
is passed on unread and nothing from the message is echoed in a refusal."""

import uuid
from datetime import datetime

from xype.v1 import common_pb2, index_pb2, session_pb2

from backend.grpc.messages import chunk_ref, response
from backend.services.indexing import Upload, UploadAccepted, UploadedChunk
from backend.services.session import MALFORMED, RefusedError
from common_lib.correlation import is_valid_correlation_id
from common_lib.domain import ChunkRef
from common_lib.errors import InvalidValueError
from common_lib.merkle import MAX_CHUNKS_PER_UPLOAD, RejectionReason


def reason_name(reason: RejectionReason) -> str:
    """The proto enum name of a stored reason (ADR-0019); a reason the proto lacks
    raises `ValueError` (a unit test covers every one)."""
    name = f"REJECTION_REASON_{reason.upper()}"
    index_pb2.RejectionReason.Value(name)
    return name


# Part of every payload's AAD (ADR-0015): the client binds `chunks[i].content` to it.
CHUNKS_FIELD = index_pb2.ChunkUpload.CHUNKS_FIELD_NUMBER


def upload(message: index_pb2.ChunkUpload, message_id: str) -> Upload:
    if len(message.chunks) > MAX_CHUNKS_PER_UPLOAD:
        raise RefusedError(MALFORMED)
    if not (is_valid_correlation_id(message_id) and is_valid_correlation_id(message.request_id)):
        raise RefusedError(MALFORMED)
    try:
        chunks = tuple(_chunk(chunk) for chunk in message.chunks)
        return Upload(
            message_id,
            message.request_id,
            uuid.UUID(message.repository_id),
            CHUNKS_FIELD,
            chunks,
        )
    except (InvalidValueError, ValueError):
        raise RefusedError(MALFORMED) from None


def _chunk(chunk: common_pb2.ChunkContent) -> UploadedChunk:
    ref = chunk.ref  # `ChunkRef` checks the ids and the line range
    return UploadedChunk(
        ChunkRef(
            uuid.UUID(ref.repository_id),
            ref.obfuscated_file_id,
            ref.lines.start,
            ref.lines.end,
            ref.chunk_hash,
            ref.leaf_node_id,
        ),
        chunk.content.key_id,
        chunk.content.nonce,
        chunk.content.ciphertext,
    )


def ack(
    now: datetime, correlation_id: str, accepted: UploadAccepted
) -> session_pb2.SessionResponse:
    """Accepted for indexing: the chunks settle later, so no id is reported indexed.
    Chunks rejected before are reported with their fixed reason (ADR-0019)."""
    rejected = [
        index_pb2.RejectedChunk(chunk=chunk_ref(ref), reason=reason_name(reason))
        for ref, reason in accepted.rejected
    ]
    out = index_pb2.IndexAck(
        request_id=accepted.request_id,
        server_root_hash=accepted.root or "",
        rejected=rejected,
    )
    return response(now, correlation_id, session_pb2.SessionResponse(index_ack=out))
