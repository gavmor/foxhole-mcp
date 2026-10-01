"""OpenTelemetry instrumentation, tracing configuration, and context propagation for Foxhole MCP."""

from __future__ import annotations

import logging
import os
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

import httpx
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.shared._otel import extract_trace_context
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer

logger = logging.getLogger(__name__)

# Alias OTLPExporter as requested
OTLPExporter = OTLPSpanExporter

_SUPPRESS_FASTMCP_TELEMETRY: ContextVar[bool] = ContextVar(
    "suppress_fastmcp_telemetry", default=False
)


@contextmanager
def suppress_fastmcp_telemetry() -> Generator[None]:
    """Context manager to suppress FastMCP server spans for a single block.

    Applies propagation_only semantics: incoming trace context is attached
    for child spans (e.g. HTTP client, database, tools), but FastMCP's own
    server span is skipped to avoid duplicate hierarchy.
    """
    token = _SUPPRESS_FASTMCP_TELEMETRY.set(True)
    try:
        yield
    finally:
        _SUPPRESS_FASTMCP_TELEMETRY.reset(token)


class PropagationOnlyMiddleware(ServerMiddleware[Any]):
    """Middleware that attaches trace context without emitting FastMCP server spans."""

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        parent_ctx = extract_trace_context(ctx.meta)
        token = otel_context.attach(parent_ctx) if parent_ctx else None
        try:
            return await call_next(ctx)
        finally:
            if token is not None:
                otel_context.detach(token)


class SmartTelemetryMiddleware(ServerMiddleware[Any]):
    """Middleware wrapping OpenTelemetryMiddleware to support block suppression."""

    def __init__(self) -> None:
        self._inner = OpenTelemetryMiddleware()

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if _SUPPRESS_FASTMCP_TELEMETRY.get():
            parent_ctx = extract_trace_context(ctx.meta)
            token = otel_context.attach(parent_ctx) if parent_ctx else None
            try:
                return await call_next(ctx)
            finally:
                if token is not None:
                    otel_context.detach(token)
        return await self._inner(ctx, call_next)


def apply_telemetry_mode(server: MCPServer, mode: str | None = None) -> None:
    """Configure server middleware according to FASTMCP_TELEMETRY_MODE.

    Modes:
    - 'off': Disable FastMCP instrumentation entirely. No server spans and no context propagation.
    - 'propagation_only': Do not emit FastMCP server spans, but extract and attach trace context.
    - 'on' (or default): Emit full FastMCP semantic convention spans.
    """
    if mode is None:
        mode = os.getenv("FASTMCP_TELEMETRY_MODE", "on").strip().lower()
    else:
        mode = mode.strip().lower()

    lowlevel = server._lowlevel_server
    # Strip any previously installed telemetry middleware
    lowlevel.middleware = [
        m
        for m in lowlevel.middleware
        if not isinstance(
            m, (OpenTelemetryMiddleware, PropagationOnlyMiddleware, SmartTelemetryMiddleware)
        )
    ]

    if mode == "off":
        logger.debug("FastMCP telemetry mode: off (instrumentation disabled)")
        return
    elif mode == "propagation_only":
        logger.debug("FastMCP telemetry mode: propagation_only")
        lowlevel.middleware.insert(0, PropagationOnlyMiddleware())
    else:
        logger.debug("FastMCP telemetry mode: on")
        lowlevel.middleware.insert(0, SmartTelemetryMiddleware())


def instrument_httpx_client(client: Any = None) -> None:
    """Instrument httpx clients with OpenTelemetry."""
    instrumentor = HTTPXClientInstrumentor()
    if client is not None:
        if hasattr(client, "_client") and client._client is not None:
            instrumentor.instrument_client(client._client)
        elif isinstance(client, (httpx.Client, httpx.AsyncClient)):
            instrumentor.instrument_client(client)
    else:
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument()


def uninstrument_httpx_client() -> None:
    """Uninstrument httpx clients."""
    instrumentor = HTTPXClientInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()


def get_tracer(name: str = "foxhole") -> Tracer:
    """Return an OpenTelemetry Tracer instance."""
    return trace.get_tracer(name)


def reset_telemetry() -> None:
    """Reset global OpenTelemetry tracer provider and uninstrument HTTPX for clean testing."""
    uninstrument_httpx_client()
    trace._TRACER_PROVIDER = None
    if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        trace._TRACER_PROVIDER_SET_ONCE._done = False

    try:
        import mcp.shared._otel as so

        if hasattr(so, "_tracer") and hasattr(so._tracer, "_real_tracer"):
            setattr(so._tracer, "_real_tracer", None)  # noqa: B010
    except ImportError:
        pass

    try:
        import sys

        fp = sys.modules.get("foxhole.tools.production")
        if fp and hasattr(fp, "tracer") and hasattr(fp.tracer, "_real_tracer"):
            setattr(fp.tracer, "_real_tracer", None)  # noqa: B010
    except Exception:
        pass


def setup_telemetry(
    service_name: str | None = None,
    exporter: (
        SpanExporter | Literal["otlp", "console", "memory", "in_memory", "none"] | str | None
    ) = None,
    span_processor: SpanProcessor | None = None,
    instrument_httpx: bool = True,
    telemetry_mode: str | None = None,
    resource_attributes: Mapping[str, Any] | None = None,
    force: bool = False,
) -> TracerProvider | None:
    """Configure TracerProvider, Resource, SpanProcessors, and HTTPXClientInstrumentor.

    Respects FASTMCP_TELEMETRY_MODE and standard OTEL environment variables:
    - OTEL_SDK_DISABLED: if 'true', returns None without configuring SDK
    - FASTMCP_TELEMETRY_MODE: if 'off', returns None
    - OTEL_SERVICE_NAME: overrides default 'foxhole' service name
    - OTEL_TRACES_EXPORTER: 'otlp', 'console', 'memory', 'none'
    - OTEL_EXPORTER_OTLP_ENDPOINT: target endpoint for OTLP traces
    """
    # 1. Respect OTEL_SDK_DISABLED
    if os.getenv("OTEL_SDK_DISABLED", "").strip().lower() in ("true", "1"):
        logger.info("OpenTelemetry SDK disabled via OTEL_SDK_DISABLED")
        return None

    # 2. Respect FASTMCP_TELEMETRY_MODE
    active_mode = (telemetry_mode or os.getenv("FASTMCP_TELEMETRY_MODE", "")).strip().lower()
    if active_mode == "off":
        logger.info("FastMCP telemetry mode is off; skipping telemetry setup")
        return None

    # If already initialized with a provider and no new exporter is requested, reuse existing
    current_tp = trace.get_tracer_provider()
    if (
        isinstance(current_tp, TracerProvider)
        and exporter is None
        and span_processor is None
        and not force
    ):
        if instrument_httpx:
            instrument_httpx_client()
        return current_tp

    # 3. Configure Resource (service.name="foxhole")
    resolved_service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "foxhole")
    attributes: dict[str, Any] = {
        SERVICE_NAME: resolved_service_name,
        "service.name": resolved_service_name,
    }
    if resource_attributes:
        attributes.update(resource_attributes)
    resource = Resource.create(attributes)

    # 4. Create TracerProvider
    provider = TracerProvider(resource=resource)

    # 5. Configure Span Processors / Exporters
    if span_processor is not None:
        provider.add_span_processor(span_processor)
    elif exporter is not None:
        if isinstance(exporter, SpanExporter):
            if isinstance(exporter, (InMemorySpanExporter, ConsoleSpanExporter)):
                provider.add_span_processor(SimpleSpanProcessor(exporter))
            else:
                provider.add_span_processor(BatchSpanProcessor(exporter))
        elif isinstance(exporter, str):
            norm = exporter.strip().lower()
            if norm in ("memory", "in_memory"):
                provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
            elif norm == "console":
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            elif norm == "otlp":
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            elif norm == "none":
                pass
            else:
                raise ValueError(f"Unknown exporter: {exporter}")
    else:
        # Check environment variables
        env_exporter = os.getenv("OTEL_TRACES_EXPORTER", "").strip().lower()
        if env_exporter == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        elif env_exporter in ("memory", "in_memory"):
            provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
        elif env_exporter == "none":
            pass
        elif env_exporter == "otlp":
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        elif os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        ):
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    # 6. Set global TracerProvider
    if hasattr(trace, "_TRACER_PROVIDER") and trace._TRACER_PROVIDER is not None:
        trace._TRACER_PROVIDER = None
        if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
            trace._TRACER_PROVIDER_SET_ONCE._done = False
    trace.set_tracer_provider(provider)

    try:
        import mcp.shared._otel as so

        if hasattr(so, "_tracer") and hasattr(so._tracer, "_real_tracer"):
            setattr(so._tracer, "_real_tracer", None)  # noqa: B010
    except ImportError:
        pass

    try:
        import sys

        fp = sys.modules.get("foxhole.tools.production")
        if fp and hasattr(fp, "tracer") and hasattr(fp.tracer, "_real_tracer"):
            setattr(fp.tracer, "_real_tracer", None)  # noqa: B010
    except Exception:
        pass

    # 7. Instrument HTTPX client for wiki/warapi
    if instrument_httpx:
        instrument_httpx_client()

    return provider


__all__ = [
    "BatchSpanProcessor",
    "ConsoleSpanExporter",
    "InMemorySpanExporter",
    "OTLPExporter",
    "OTLPSpanExporter",
    "PropagationOnlyMiddleware",
    "SimpleSpanProcessor",
    "SmartTelemetryMiddleware",
    "Span",
    "SpanExporter",
    "SpanKind",
    "SpanProcessor",
    "StatusCode",
    "Tracer",
    "apply_telemetry_mode",
    "get_tracer",
    "instrument_httpx_client",
    "reset_telemetry",
    "setup_telemetry",
    "suppress_fastmcp_telemetry",
    "uninstrument_httpx_client",
]
