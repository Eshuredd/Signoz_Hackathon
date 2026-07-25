from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ReadableSpan, SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .config import PreflightConfig
from .scenarios import PreflightScenario


class PreflightExportError(Exception):
    """Raised when OTLP export does not complete."""


@dataclass(frozen=True)
class EmissionResult:
    trace_id: str
    root_span_id: str
    span_ids_by_name: dict[str, str]
    parent_span_ids_by_name: dict[str, str | None]
    expected_attributes_by_name: dict[str, dict[str, object]]
    intentionally_absent_attributes_by_name: dict[str, tuple[str, ...]]
    completed_span_count: int
    exported: bool
    exported_at: str


def emit_scenario(
    scenario: PreflightScenario,
    config: PreflightConfig,
    *,
    otlp_exporter_factory: Callable[..., object] = OTLPSpanExporter,
) -> EmissionResult:
    completed_spans, trace_id = _create_completed_spans(scenario, config)
    _verify_completed_spans(scenario, completed_spans)
    otlp_exporter = otlp_exporter_factory(endpoint=config.otlp_endpoint, timeout=config.otlp_timeout_seconds)
    try:
        export_result = otlp_exporter.export(completed_spans)  # type: ignore[attr-defined]
        if export_result != SpanExportResult.SUCCESS:
            raise PreflightExportError("OTLP trace export did not return SpanExportResult.SUCCESS.")
        if not otlp_exporter.force_flush(timeout_millis=int(config.otlp_timeout_seconds * 1000)):  # type: ignore[attr-defined]
            raise PreflightExportError("OTLP trace exporter force_flush did not succeed.")
    finally:
        otlp_exporter.shutdown()  # type: ignore[attr-defined]

    spans_by_name = {span.name: span for span in completed_spans}
    return EmissionResult(
        trace_id=trace_id,
        root_span_id=_readable_span_id(spans_by_name["agent.run"]),
        span_ids_by_name={name: _readable_span_id(span) for name, span in spans_by_name.items()},
        parent_span_ids_by_name={name: _parent_span_id(span) for name, span in spans_by_name.items()},
        expected_attributes_by_name={spec.name: dict(spec.attributes) for spec in scenario.spans},
        intentionally_absent_attributes_by_name={spec.name: tuple(spec.intentionally_absent_attributes) for spec in scenario.spans},
        completed_span_count=len(completed_spans),
        exported=True,
        exported_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _create_completed_spans(scenario: PreflightScenario, config: PreflightConfig) -> tuple[tuple[ReadableSpan, ...], str]:
    provider = TracerProvider(resource=Resource.create({"service.name": config.service_name}))
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    tracer = provider.get_tracer("traceguard.gate3.preflight")
    live_spans: dict[str, object] = {}
    try:
        for spec in scenario.spans:
            if spec.name in live_spans:
                raise PreflightExportError(f"Duplicate span name in preflight scenario: {spec.name}")
            if spec.parent is None:
                parent_context = None
            else:
                parent = live_spans.get(spec.parent)
                if parent is None:
                    raise PreflightExportError(f"Unknown or out-of-order parent span in preflight scenario: {spec.parent}")
                parent_context = trace.set_span_in_context(parent)  # type: ignore[arg-type]
            live_spans[spec.name] = tracer.start_span(spec.name, context=parent_context, attributes=spec.attributes)
        for span in reversed(list(live_spans.values())):
            span.end()  # type: ignore[attr-defined]
        completed = tuple(memory_exporter.get_finished_spans())
        trace_ids = {_readable_trace_id(span) for span in completed}
        if len(trace_ids) != 1:
            raise PreflightExportError("Completed preflight spans did not share exactly one trace ID.")
        return completed, next(iter(trace_ids))
    except PreflightExportError:
        raise
    except Exception as exc:
        raise PreflightExportError("Failed to create local preflight trace spans.") from exc
    finally:
        try:
            provider.shutdown()
        except Exception:
            pass


def _verify_completed_spans(scenario: PreflightScenario, spans: tuple[ReadableSpan, ...]) -> None:
    if len(spans) != 3:
        raise PreflightExportError("Preflight scenario did not complete exactly three spans.")
    spans_by_name = {span.name: span for span in spans}
    if len(spans_by_name) != len(spans):
        raise PreflightExportError("Preflight scenario produced duplicate span names.")
    roots = [span for span in spans if _parent_span_id(span) is None]
    if len(roots) != 1:
        raise PreflightExportError("Preflight scenario must produce exactly one root span.")
    if roots[0].name != "agent.run":
        raise PreflightExportError("Preflight scenario root span must be agent.run.")
    if sum(1 for spec in scenario.spans if spec.parent is None) != 1:
        raise PreflightExportError("Preflight scenario specification must contain exactly one root.")
    trace_ids = {_readable_trace_id(span) for span in spans}
    if len(trace_ids) != 1:
        raise PreflightExportError("Completed preflight spans did not share exactly one trace ID.")
    span_ids = [_readable_span_id(span) for span in spans]
    if any(not span_id or span_id == "0" * 16 for span_id in span_ids) or len(set(span_ids)) != len(span_ids):
        raise PreflightExportError("Completed preflight spans must have non-empty unique span IDs.")
    for spec in scenario.spans:
        if spec.name not in spans_by_name:
            raise PreflightExportError(f"Preflight span was not completed: {spec.name}")
        if spec.parent is not None:
            parent = spans_by_name.get(spec.parent)
            if parent is None:
                raise PreflightExportError(f"Unknown parent span in preflight scenario: {spec.parent}")
            if _parent_span_id(spans_by_name[spec.name]) != _readable_span_id(parent):
                raise PreflightExportError(f"Preflight span {spec.name} did not use expected parent {spec.parent}.")


def _readable_trace_id(span: ReadableSpan) -> str:
    return f"{span.context.trace_id:032x}"


def _readable_span_id(span: ReadableSpan) -> str:
    return f"{span.context.span_id:016x}"


def _parent_span_id(span: ReadableSpan) -> str | None:
    if span.parent is None:
        return None
    return f"{span.parent.span_id:016x}"
