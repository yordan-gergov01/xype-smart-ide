"""Errors by meaning. Services raise these; only the transport layers map them to
HTTP or gRPC status codes."""


class XypeError(Exception):
    """Root of every typed domain error raised by XYPE server code."""


class InvalidValueError(XypeError):
    """A domain value was constructed with invalid data."""
