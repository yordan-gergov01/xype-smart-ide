"""ASGI middleware around every HTTP request (ADR-0011).

Order, outermost first (see `backend.main`):

    RequestContextMiddleware   correlation id, server span, RED metrics, access line
    UnhandledErrorMiddleware   unexpected exception -> logged by type, generic 500

Nothing here reads or logs a request or response body, query string, headers other
than the correlation and trace ones, or an exception message. Every label and span
attribute comes from a fixed set: known methods, route templates, status codes.
"""

import time
from http import HTTPStatus

import structlog
from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Histogram
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common_lib.correlation import (
    CORRELATION_ID_HEADER,
    is_valid_correlation_id,
    new_correlation_id,
)
from common_lib.tracing import current_trace_id

access_logger = structlog.get_logger("backend.access")
logger = structlog.get_logger(__name__)

# Probed every few seconds; their access lines would drown everything else.
QUIET_ROUTES = frozenset({"/healthz", "/readyz", "/metrics"})
UNMATCHED_ROUTE = "<unmatched>"
# Any other method token a client sends is reported as _OTHER (bounded cardinality).
KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
OTHER_METHOD = "_OTHER"  # OpenTelemetry semantic-convention value
# Set by UnhandledErrorMiddleware when a request failed after its response started.
FAILED_STATE_KEY = "xype_failed"
# W3C trace context only: no baggage, which would carry client text into later hops.
_PROPAGATOR = TraceContextTextMapPropagator()


class HttpMetrics:
    """RED metrics with a fixed, low-cardinality label set (route templates, not paths)."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.requests = Counter(
            "xype_http_requests_total",
            "HTTP requests by method, route and status code.",
            ["method", "route", "status"],
            registry=registry,
        )
        self.duration = Histogram(
            "xype_http_request_duration_seconds",
            "HTTP request duration by method and route.",
            ["method", "route"],
            registry=registry,
        )


class RequestContextMiddleware:
    """Correlation id, server span, metrics and one metadata-only access line per request.

    A valid incoming `x-correlation-id` is reused so callers can correlate their own
    logs; otherwise a new one is generated and returned in the response.
    """

    def __init__(self, app: ASGIApp, *, tracer: Tracer, metrics: HttpMetrics) -> None:
        self.app = app
        self.tracer = tracer
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        incoming = headers.get(CORRELATION_ID_HEADER, "")
        correlation_id = incoming if is_valid_correlation_id(incoming) else new_correlation_id()
        method = scope["method"] if scope["method"] in KNOWN_METHODS else OTHER_METHOD
        status = HTTPStatus.INTERNAL_SERVER_ERROR.value
        started = time.perf_counter()

        async def send_with_correlation_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (CORRELATION_ID_HEADER.encode(), correlation_id.encode()),
                ]
            await send(message)

        # Exceptions are never recorded on the span: their messages can hold payloads.
        with (
            self.tracer.start_as_current_span(
                f"HTTP {method}",
                kind=SpanKind.SERVER,
                context=_PROPAGATOR.extract(headers),
                record_exception=False,
                set_status_on_exception=False,
            ) as span,
            structlog.contextvars.bound_contextvars(
                correlation_id=correlation_id, trace_id=current_trace_id()
            ),
        ):
            try:
                await self.app(scope, receive, send_with_correlation_id)
            finally:
                if scope.get("state", {}).get(FAILED_STATE_KEY):
                    status = HTTPStatus.INTERNAL_SERVER_ERROR.value
                route = _route_template(scope)
                _finish_span(span, method, route, status, correlation_id)
                self._record(method, route, status, time.perf_counter() - started)

    def _record(self, method: str, route: str, status: int, duration: float) -> None:
        """RED metrics and the metadata-only access line."""
        self.metrics.requests.labels(method, route, str(status)).inc()
        self.metrics.duration.labels(method, route).observe(duration)
        log = access_logger.debug if route in QUIET_ROUTES else access_logger.info
        log(
            "http.request",
            method=method,
            route=route,
            status=status,
            duration_ms=round(duration * 1000, 1),
        )


class UnhandledErrorMiddleware:
    """Turns an exception no handler caught into a generic 500.

    The exception is logged once, by type and frames only (the logging pipeline drops
    messages). The client gets the correlation id to quote, nothing else.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("http.unhandled_error", route=_route_template(scope))
            if response_started:
                # Headers are already sent; the server closes the connection. Record
                # the request as failed, whatever status was sent.
                scope.setdefault("state", {})[FAILED_STATE_KEY] = True
                return
            response = JSONResponse(
                {"code": "INTERNAL_ERROR", "detail": "Internal server error."},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            await response(scope, receive, send)


def _finish_span(span: Span, method: str, route: str, status: int, correlation_id: str) -> None:
    span.update_name(f"HTTP {method} {route}")
    span.set_attributes(
        {
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status,
            "xype.correlation_id": correlation_id,
        }
    )
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        span.set_status(StatusCode.ERROR)


def _route_template(scope: Scope) -> str:
    """The matched route's template (`/items/{id}`), never the concrete path."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE
