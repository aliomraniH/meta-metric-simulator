"""OpenTelemetry tracer setup with W3C trace propagation.

The Claude Agent SDK runs subagents in subprocesses (CLAUDE_CODE_FORK_SUBAGENT=1).
For traces to follow the work into the subagent, we configure W3C tracecontext
propagation here and rely on the SDK reading TRACEPARENT from the environment.

This module is intentionally side-effecty when init_telemetry() is called:
configuring the global tracer provider is a one-time setup the rest of the
codebase depends on.  Repeat calls are no-ops.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_initialised = False
_tracer: Any = None


def init_telemetry(service_name: str | None = None) -> Any:
    """Configure the global tracer provider.  Returns the tracer.

    Safe to call multiple times.  When OTEL_EXPORTER_OTLP_ENDPOINT is unset
    (e.g. local dev) the provider is configured with a no-op exporter so
    spans are still created (and inspectable in tests) but not shipped.
    """
    global _initialised, _tracer
    if _initialised:
        return _tracer

    try:
        from opentelemetry import propagate, trace
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
    except ImportError:  # pragma: no cover — opentelemetry pinned in pyproject
        log.warning("opentelemetry not installed; telemetry disabled")
        _initialised = True
        return None

    resource = Resource.create({
        "service.name": service_name or os.environ.get("OTEL_SERVICE_NAME", "reels-sim-mcp"),
        "service.version": os.environ.get("APP_VERSION", "0.1.0"),
        "deployment.environment": os.environ.get("ENV", "development"),
    })

    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
            provider.add_span_processor(BatchSpanProcessor(exporter))
            log.info("OTLP exporter configured at %s", endpoint)
        except Exception as exc:  # pragma: no cover
            log.warning("OTLP exporter init failed: %s — falling back to no-op", exc)

    trace.set_tracer_provider(provider)

    # W3C tracecontext + baggage so subagent processes can pick up the trace.
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    _tracer = trace.get_tracer("reels-sim-mcp")
    _initialised = True

    log.debug(
        "telemetry initialised (otel_enabled=%s, fork_subagent=%s)",
        os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY"),
        os.environ.get("CLAUDE_CODE_FORK_SUBAGENT"),
    )
    return _tracer


def get_tracer() -> Any:
    if not _initialised:
        return init_telemetry()
    return _tracer


def inject_traceparent_into_env(env: dict[str, str]) -> dict[str, str]:
    """Inject the current span's traceparent into an env dict.

    Used by the orchestrator when spawning Claude Agent SDK subprocesses so
    spans created by subagents stitch back into the parent trace.
    """
    try:
        from opentelemetry import propagate
    except ImportError:
        return env
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    env.update(carrier)
    return env
