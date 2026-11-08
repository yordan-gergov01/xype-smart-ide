"""Correlation ids: one id follows a request across REST, gRPC, RabbitMQ and workers."""

import re
from uuid import uuid4

CORRELATION_ID_HEADER = "x-correlation-id"

# Correlation ids travel in headers, message metadata and log lines: short and plain.
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def new_correlation_id() -> str:
    """Generate a fresh correlation id."""
    return uuid4().hex


def is_valid_correlation_id(value: str) -> bool:
    """Whether a client-supplied correlation id is safe to reuse."""
    return _CORRELATION_ID.fullmatch(value) is not None
