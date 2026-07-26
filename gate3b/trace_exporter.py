from __future__ import annotations

from typing import Callable

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ReadableSpan, SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .config import Gate3BConfig
from .models import TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, Gate3BInfrastructureError, RuntimeScenario, TraceEmissionResult, now_iso


class Gate3BTraceExportError(Gate3BInfrastructureError):
    """Trace export failed."""


def emit_traces(
    scenario: RuntimeScenario,
    config: Gate3BConfig,
    *,
    otlp_exporter_factory: Callable[..., object] = OTLPSpanExporter,
) -> TraceEmissionResult:
    completed = _create_completed_spans(scenario, config)
    _verify_completed_spans(scenario, completed)
    exporter = otlp_exporter_factory(endpoint=config.trace_otlp_endpoint, timeout=config.otlp_timeout_seconds)
    try:
        result = exporter.export(completed)  # type: ignore[attr-defined]
        if result != SpanExportResult.SUCCESS:
            raise Gate3BTraceExportError("OTLP trace export did not return SpanExportResult.SUCCESS.")
        if not exporter.force_flush(timeout_millis=int(config.otlp_timeout_seconds * 1000)):  # type: ignore[attr-defined]
            raise Gate3BTraceExportError("OTLP trace exporter force_flush did not succeed.")
    finally:
        exporter.shutdown()  # type: ignore[attr-defined]
    return _emission_result(scenario, completed)


def _create_completed_spans(scenario: RuntimeScenario, config: Gate3BConfig) -> tuple[ReadableSpan, ...]:
    provider = TracerProvider(resource=Resource.create({"service.name": config.service_name}))
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    tracer = provider.get_tracer("traceguard.gate3b")
    try:
        for index, _trace_spec in enumerate(scenario.definition.trace_plan):
            root_attrs = _base_attrs(scenario) | {"agent.run_id": scenario.agent_run_id, "agent.name": "traceguard-gate3b", "agent.status": "success"}
            root = tracer.start_span("agent.run", attributes=root_attrs)
            ctx = trace.set_span_in_context(root)
            tool = tracer.start_span("tool.call", context=ctx, attributes=_base_attrs(scenario) | {"tool.status": "success"})
            model = tracer.start_span("model.call", context=ctx, attributes=_base_attrs(scenario) | {"gen_ai.request.model": "gpt-gate3b", "gen_ai.usage.input_tokens": 7 + index, "gen_ai.usage.output_tokens": 11 + index})
            model.end()
            tool.end()
            root.end()
        return tuple(memory_exporter.get_finished_spans())
    except Exception as exc:
        raise Gate3BTraceExportError("Failed to create Gate 3B local trace spans.") from exc
    finally:
        try:
            provider.shutdown()
        except Exception:
            pass


def _base_attrs(scenario: RuntimeScenario) -> dict[str, object]:
    return {
        TRACE_BATCH_ATTR: scenario.batch_id,
        TRACE_SCENARIO_ATTR: scenario.scenario_id,
        TRACE_SCENARIO_NAME_ATTR: scenario.name,
    }


def _verify_completed_spans(scenario: RuntimeScenario, spans: tuple[ReadableSpan, ...]) -> None:
    trace_ids = {_trace_id(span) for span in spans}
    if len(trace_ids) != scenario.definition.expected_trace_count:
        raise Gate3BTraceExportError("Local trace creation produced the wrong number of traces.")
    by_trace: dict[str, list[ReadableSpan]] = {}
    for span in spans:
        by_trace.setdefault(_trace_id(span), []).append(span)
    for trace_id, group in by_trace.items():
        if len(group) != 3:
            raise Gate3BTraceExportError(f"Trace {trace_id} did not contain exactly three spans.")
        by_name = {span.name: span for span in group}
        if set(by_name) != {"agent.run", "tool.call", "model.call"}:
            raise Gate3BTraceExportError(f"Trace {trace_id} did not contain the canonical span names.")
        root = by_name["agent.run"]
        if _parent_span_id(root) is not None:
            raise Gate3BTraceExportError("agent.run root unexpectedly has a parent.")
        if _parent_span_id(by_name["tool.call"]) != _span_id(root) or _parent_span_id(by_name["model.call"]) != _span_id(root):
            raise Gate3BTraceExportError("Tool/model spans did not point to the trace root.")
        span_ids = [_span_id(span) for span in group]
        if len(set(span_ids)) != 3 or any(not item or item == "0" * 16 for item in span_ids):
            raise Gate3BTraceExportError("Span IDs must be non-empty and unique within each trace.")
        for span in group:
            attrs = dict(span.attributes or {})
            for key in (TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR):
                if key not in attrs:
                    raise Gate3BTraceExportError(f"Span {span.name} is missing {key}.")


def _emission_result(scenario: RuntimeScenario, spans: tuple[ReadableSpan, ...]) -> TraceEmissionResult:
    trace_ids = tuple(sorted({_trace_id(span) for span in spans}))
    by_trace: dict[str, dict[str, ReadableSpan]] = {}
    for span in spans:
        by_trace.setdefault(_trace_id(span), {})[span.name] = span
    return TraceEmissionResult(
        scenario_name=scenario.name,
        agent_run_id=scenario.agent_run_id,
        service_name=_service_name(spans),
        emitted_trace_ids=trace_ids,
        root_span_ids_by_trace_id={trace_id: _span_id(by_name["agent.run"]) for trace_id, by_name in by_trace.items()},
        span_ids_by_trace_id_and_name={trace_id: {name: _span_id(span) for name, span in by_name.items()} for trace_id, by_name in by_trace.items()},
        parent_span_ids_by_trace_id_and_name={trace_id: {name: _parent_span_id(span) for name, span in by_name.items()} for trace_id, by_name in by_trace.items()},
        expected_attributes_by_trace_id_and_name={trace_id: {name: dict(span.attributes or {}) for name, span in by_name.items()} for trace_id, by_name in by_trace.items()},
        exported_at=now_iso(),
    )


def _trace_id(span: ReadableSpan) -> str:
    return f"{span.context.trace_id:032x}"


def _span_id(span: ReadableSpan) -> str:
    return f"{span.context.span_id:016x}"


def _parent_span_id(span: ReadableSpan) -> str | None:
    if span.parent is None:
        return None
    return f"{span.parent.span_id:016x}"


def _service_name(spans: tuple[ReadableSpan, ...]) -> str:
    for span in spans:
        resource = getattr(span, "resource", None)
        attrs = getattr(resource, "attributes", {}) if resource is not None else {}
        value = attrs.get("service.name") if attrs else None
        if value:
            return str(value)
    return ""
