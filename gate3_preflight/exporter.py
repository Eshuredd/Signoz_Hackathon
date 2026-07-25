from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from .config import PreflightConfig
from .scenarios import PreflightScenario


class PreflightExportError(Exception):
    """Raised when OTLP export does not complete."""


@dataclass(frozen=True)
class EmissionResult:
    trace_id: str
    span_ids: dict[str, str]


def emit_scenario(scenario: PreflightScenario, config: PreflightConfig) -> EmissionResult:
    provider = TracerProvider(resource=Resource.create({"service.name": config.service_name}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=config.otlp_endpoint)))
    tracer = provider.get_tracer("traceguard.gate3.preflight")
    span_ids: dict[str, str] = {}
    try:
        with tracer.start_as_current_span("agent.run", attributes=scenario.spans[0].attributes) as root:
            span_ids["agent.run"] = _span_id(root)
            trace_id = _trace_id(root)
            for spec in scenario.spans[1:]:
                with tracer.start_as_current_span(spec.name, attributes=spec.attributes) as child:
                    span_ids[spec.name] = _span_id(child)
        provider.force_flush()
        provider.shutdown()
    except Exception as exc:
        raise PreflightExportError("Failed to export live preflight trace through OTLP.") from exc
    return EmissionResult(trace_id=trace_id, span_ids=span_ids)


def _trace_id(span: object) -> str:
    return f"{span.get_span_context().trace_id:032x}"  # type: ignore[attr-defined]


def _span_id(span: object) -> str:
    return f"{span.get_span_context().span_id:016x}"  # type: ignore[attr-defined]
