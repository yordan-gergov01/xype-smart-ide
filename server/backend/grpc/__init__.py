"""The gRPC transport (ADR-0013): the `Session` stream handler and its server. Transport
only: the rules live in `backend.services.session`."""

from backend.grpc.metrics import StreamMetrics
from backend.grpc.server import METHOD, SERVICE, create_grpc_server
from backend.grpc.stream import SessionStream

__all__ = ["METHOD", "SERVICE", "SessionStream", "StreamMetrics", "create_grpc_server"]
