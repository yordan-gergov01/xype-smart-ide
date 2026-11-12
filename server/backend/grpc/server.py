"""The gRPC server of the backend process (ADR-0013): one method, served over TLS with
the backend's certificate (ADR-0036), started and stopped by the app's lifespan."""

import grpc
from grpc import aio
from xype.v1 import session_pb2

from backend.core.settings import BackendSettings
from backend.grpc.stream import SessionStream

SERVICE = "xype.v1.SessionService"
METHOD = "Session"
# A message carries at most a few encrypted chunks (64 KiB each, ADR-0019). With the
# stream's read-ahead (`INBOX`) this bounds the memory one stream can hold.
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_STREAMS_PER_CONNECTION = 16


def create_grpc_server(settings: BackendSettings, stream: SessionStream) -> tuple[aio.Server, int]:
    """The server and the port it bound (useful with port 0 in tests). Not started."""
    server = aio.server(
        # Each stream holds a few MiB at most (`INBOX` messages of the receive cap); the cap
        # on streams keeps the process within its memory limit. Beyond it new calls get
        # RESOURCE_EXHAUSTED.
        maximum_concurrent_rpcs=settings.stream_max_concurrent,
        options=[
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
            # One Session stream per IDE window; a connection with more is not ours.
            ("grpc.max_concurrent_streams", MAX_STREAMS_PER_CONNECTION),
            # Refuses clients that ping more often than every 10 s (keepalive abuse).
            ("grpc.http2.min_ping_interval_without_data_ms", 10_000),
        ],
    )
    # A bound async generator method: grpcio checks for one to run it as async.
    handler = grpc.stream_stream_rpc_method_handler(
        stream.handle,
        request_deserializer=session_pb2.SessionRequest.FromString,
        response_serializer=session_pb2.SessionResponse.SerializeToString,
    )
    server.add_generic_rpc_handlers(
        (grpc.method_handlers_generic_handler(SERVICE, {METHOD: handler}),)
    )
    credentials = grpc.ssl_server_credentials(
        [(settings.tls_key_file.read_bytes(), settings.tls_cert_file.read_bytes())]
    )
    port = server.add_secure_port(settings.grpc_listen_address, credentials)
    return server, port
