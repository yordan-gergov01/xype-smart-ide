"""Structured logging, configured once per service at startup (ADR-0011).

structlog renders every line, including those of libraries that use the standard
`logging` module, through the same processors. Output goes to stdout as JSON lines
(or readable console lines locally). Context such as the correlation id is bound
with `structlog.contextvars` and appears on every line logged while it is bound.

Logs carry metadata only (.claude/rules/08-observability.md). Two safety nets back
that rule: values of sensitive keys are redacted, and exceptions are logged as their
type and stack frames without the message, because messages can hold payloads.
"""

import logging
import sys
import traceback
from collections.abc import Mapping
from typing import Any, Literal, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

LogFormat = Literal["json", "console"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

REDACTED = "[REDACTED]"
MAX_FRAMES = 15

# A safety net, not a licence: matching is case-insensitive at any nesting depth,
# inside mappings, lists and tuples. Keys that name content are matched exactly
# ("code", not "status_code"); keys that hold credentials are matched by part
# ("db_password", "x_api_key", "auth_token").
CONTENT_KEYS = frozenset(
    {
        "answer",
        "body",
        "code",
        "content",
        "filename",
        "headers",
        "path",
        "prompt",
        "query",
        "snippet",
        "text",
    }
)
SECRET_KEY_PARTS = (
    "apikey",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "passwd",
    "password",
    "secret",
    "token",
)


def is_sensitive_key(key: str) -> bool:
    """Whether a log field's value must be redacted."""
    lowered = key.lower()
    return lowered in CONTENT_KEYS or any(part in lowered for part in SECRET_KEY_PARTS)


# Third-party loggers and their minimum level. SQLAlchemy logs SQL with parameters and
# httpx logs URLs below WARNING; uvicorn's access log is replaced by our own line.
LIBRARY_LOG_LEVELS: Mapping[str, int] = {
    "uvicorn": logging.INFO,
    "uvicorn.error": logging.INFO,
    "uvicorn.access": logging.WARNING,
    "sqlalchemy": logging.WARNING,
    "asyncpg": logging.WARNING,
    "aio_pika": logging.WARNING,
    "aiormq": logging.WARNING,
    # Its error lines ("Task exception was never retrieved") embed repr(exception).
    "asyncio": logging.CRITICAL,
    "opentelemetry": logging.WARNING,
    # Request lines include full URLs with query strings.
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
}


def redact_sensitive(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Replace the values of sensitive keys. Nested values are copied, never changed in
    place, so redaction does not alter the caller's objects."""
    return {key: _redact_field(key, value) for key, value in event_dict.items()}


def _redact_field(key: str, value: Any) -> Any:  # reason: log values have any type
    if is_sensitive_key(key):
        return REDACTED
    return _redact_value(value)


def _redact_value(value: Any) -> Any:  # reason: log values have any type
    if isinstance(value, Mapping):
        return {k: _redact_field(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        # A plain list: subclasses (namedtuples) may not rebuild from an iterable.
        return [_redact_value(item) for item in value]
    return value


def exception_without_message(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Turn `exc_info` into the exception type and its frames; drop the message.

    Never switch to a renderer that captures frame locals (structlog `dict_tracebacks`,
    `show_locals`): locals hold plaintext code (common_lib.secret_bytes).
    """
    exc_info = event_dict.pop("exc_info", None)
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    if isinstance(exc_info, tuple) and exc_info[0] is not None:
        exc_type, _exc, tb = exc_info
        frames = traceback.extract_tb(tb)[-MAX_FRAMES:]
        event_dict["error_type"] = f"{exc_type.__module__}.{exc_type.__qualname__}"
        event_dict["error_frames"] = [f"{f.filename}:{f.lineno}:{f.name}" for f in frames]
    return event_dict


def _drop_color_message(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Uvicorn adds a copy of its message with colour codes; keep only the plain one."""
    event_dict.pop("color_message", None)
    return event_dict


def _add_service(service: str) -> Processor:
    def add_service(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    return add_service


def configure_logging(
    *,
    service: str,
    level: LogLevel = "INFO",
    log_format: LogFormat = "json",
    stream: TextIO | None = None,
) -> None:
    """Route structlog and standard-library logging through one pipeline to `stream`."""
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _drop_color_message,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service(service),
        exception_without_message,
        redact_sensitive,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # A record that fails to format must not be printed raw (msg and args) to stderr.
    logging.raiseExceptions = False
    for name, library_level in LIBRARY_LOG_LEVELS.items():
        library_logger = logging.getLogger(name)
        # Uvicorn installs its own handlers first; remove them so each line passes once.
        library_logger.handlers.clear()
        library_logger.propagate = True
        library_logger.setLevel(max(library_level, logging.getLevelName(level)))

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
