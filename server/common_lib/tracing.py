"""OpenTelemetry tracing, configured once per service (ADR-0011).

Spans carry ids, routes, sizes and outcomes only, never payloads. Without an OTLP
endpoint spans are still created (so trace ids reach the logs) but not exported.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_TRACES_PATH = "/v1/traces"
EXPORT_TIMEOUT_SECONDS = 5


def configure_tracing(*, service: str, otlp_endpoint: str | None) -> TracerProvider:
    """Create this service's tracer provider; export over OTLP/HTTP when configured.

    The provider is returned, not installed globally, so tests and several app
    instances in one process do not interfere; callers shut it down on exit.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if otlp_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint.rstrip("/") + OTLP_TRACES_PATH,
            timeout=EXPORT_TIMEOUT_SECONDS,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def current_trace_id() -> str | None:
    """The active span's trace id as 32 hex characters, or None outside a span."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
