"""Protobuf to and from the session service's values. Only fixed strings and ids go into
plain fields (ADR-0013); nothing from a request is echoed back."""

from datetime import datetime

from google.protobuf.timestamp_pb2 import Timestamp
from xype.v1 import common_pb2, session_pb2

from backend.services.session import HelloRequest
from common_lib.correlation import is_valid_correlation_id
from common_lib.domain import uuid7

# Top-level repository ids, checked against those authorized at Hello. Nested
# `ChunkRef`s join this when their message is handled (slices 7, 8); until then those
# messages are refused as unsupported.
_REPOSITORY_FIELDS = {"chunk_upload", "merkle_root_report", "merkle_tree_report"}


def kind(request: session_pb2.SessionRequest) -> str:
    """The `oneof body` field set, or "none": a fixed, low-cardinality metrics label."""
    return request.WhichOneof("body") or "none"


def hello_request(hello: session_pb2.Hello) -> HelloRequest:
    return HelloRequest(
        session_key_id=hello.session_key_id,
        repository_ids=tuple(hello.repository_ids),
        repository_roots=dict(hello.repository_roots),
    )


def repository_ids(request: session_pb2.SessionRequest) -> list[str]:
    body = kind(request)
    if body not in _REPOSITORY_FIELDS:
        return []
    message = getattr(request, body)
    return [message.repository_id]


def correlation_of(request: session_pb2.SessionRequest | None, fallback: str) -> str:
    """The id a response answers: the request's message id when it is a safe id."""
    if request is not None and is_valid_correlation_id(request.envelope.message_id):
        return request.envelope.message_id
    return fallback


def response(
    now: datetime, correlation_id: str, out: session_pb2.SessionResponse
) -> session_pb2.SessionResponse:
    """`out` with its envelope: a new message id, what it answers, and when it was sent."""
    sent_at = Timestamp()
    sent_at.FromDatetime(now)
    out.envelope.CopyFrom(
        common_pb2.Envelope(
            message_id=str(uuid7(now)), correlation_id=correlation_id, sent_at=sent_at
        )
    )
    return out


def stream_error(
    now: datetime,
    correlation_id: str,
    code: session_pb2.StreamError.Code,
    message: str,
    retry_after_ms: int = 0,
) -> session_pb2.SessionResponse:
    error = session_pb2.StreamError(code=code, message=message, retry_after_ms=retry_after_ms)
    return response(now, correlation_id, session_pb2.SessionResponse(error=error))


def token_refresh(
    now: datetime, correlation_id: str, expires_at: datetime
) -> session_pb2.SessionResponse:
    stamp = Timestamp()
    stamp.FromDatetime(expires_at)
    notice = session_pb2.TokenRefreshRequired(expires_at=stamp)
    return response(now, correlation_id, session_pb2.SessionResponse(token_refresh_required=notice))
