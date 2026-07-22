from __future__ import annotations

import os
import sys
import uuid
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode


DEFAULT_OTLP_HTTP_BASE = "http://localhost:4318"
TRACE_PATH = "/v1/traces"


class RelationshipFixtureError(RuntimeError):
    pass


def trace_endpoint() -> str:
    endpoint = os.getenv(
        "TRACEGUARD_OTLP_TRACES_ENDPOINT",
        os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", ""),
    )
    if endpoint:
        return endpoint
    base = (
        os.getenv("TRACEGUARD_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or DEFAULT_OTLP_HTTP_BASE
    ).rstrip("/")
    if base.endswith(TRACE_PATH):
        return base
    if base.endswith("/v1"):
        return f"{base}/traces"
    return f"{base}{TRACE_PATH}"


def timeout_millis() -> int:
    raw_value = os.getenv(
        "TRACEGUARD_OTLP_TIMEOUT_SECONDS",
        os.getenv("OTEL_EXPORTER_OTLP_TIMEOUT", "10"),
    )
    try:
        seconds = float(raw_value)
    except ValueError as exc:
        raise RelationshipFixtureError("OTLP timeout must be numeric seconds.") from exc
    if seconds <= 0:
        raise RelationshipFixtureError("OTLP timeout must be greater than zero.")
    return int(seconds * 1000)


def resource() -> Resource:
    return Resource.create(
        {
            "service.name": "traceguard-gate2-fixture",
            "service.version": "0.1.0",
            "deployment.environment.name": "local",
        }
    )


def create_relationship_spans(otel_resource: Resource, run_id: str) -> tuple[list[Any], str]:
    memory_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=otel_resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    tracer = tracer_provider.get_tracer("traceguard.gate2.relationship_fixture")

    attributes = {
        "traceguard.project": "TraceGuard",
        "traceguard.gate": "2",
        "traceguard.run_id": run_id,
        "agent.run_id": run_id,
        "traceguard.fixture": "relationship",
    }
    with tracer.start_as_current_span("gate2.test.root") as root:
        root.set_attributes(attributes)
        root.set_status(Status(StatusCode.OK))
        trace_id = f"{root.get_span_context().trace_id:032x}"
        with tracer.start_as_current_span("gate2.test.child") as child:
            child.set_attributes(attributes)
            child.set_status(Status(StatusCode.OK))

    spans = list(memory_exporter.get_finished_spans())
    tracer_provider.shutdown()
    if len(spans) != 2:
        raise RelationshipFixtureError(f"Expected exactly two finished spans, got {len(spans)}.")
    return spans, trace_id


def export_trace(spans: list[Any], endpoint: str, timeout_ms: int) -> None:
    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=timeout_ms / 1000)
    try:
        result = exporter.export(spans)
        if result is not SpanExportResult.SUCCESS:
            raise RelationshipFixtureError(
                f"Relationship fixture export to {endpoint} failed: {result!s}"
            )
        if not exporter.force_flush(timeout_millis=timeout_ms):
            raise RelationshipFixtureError("Trace exporter force_flush returned false.")
    finally:
        exporter.shutdown()


def main() -> int:
    run_id = os.getenv("TRACEGUARD_GATE2_FIXTURE_RUN_ID") or f"gate2-{uuid.uuid4()}"
    try:
        endpoint = trace_endpoint()
        timeout_ms = timeout_millis()
        spans, trace_id = create_relationship_spans(resource(), run_id)
        export_trace(spans, endpoint, timeout_ms)
    except Exception as exc:
        print(f"ERROR: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    root = next(span for span in spans if span.name == "gate2.test.root")
    child = next(span for span in spans if span.name == "gate2.test.child")
    print(f"SUCCESS: exported Gate 2 relationship fixture run_id={run_id}")
    print(f"trace_id={trace_id}")
    print(f"root_span_id={root.get_span_context().span_id:016x}")
    print(f"child_span_id={child.get_span_context().span_id:016x}")
    print(f"child_parent_span_id={child.parent.span_id:016x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
