"""Errors by meaning. Services raise these; only the transport layers map them to
HTTP or gRPC status codes."""


class XypeError(Exception):
    """Root of every typed domain error raised by XYPE server code."""


class InvalidValueError(XypeError):
    """A domain value was constructed with invalid data."""


class DependencyUnavailableError(XypeError):
    """A store, broker or model server could not be reached.

    Adapters raise it `from None` with the dependency's name only, so no driver
    message (hosts, credentials, statements) travels with it.
    """

    def __init__(self, dependency: str) -> None:
        super().__init__(f"{dependency} is unavailable")
        self.dependency = dependency
