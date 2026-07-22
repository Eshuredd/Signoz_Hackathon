from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    MetricExportResult,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Status, StatusCode


SERVICE_NAME = "traceguard-gate1"
SERVICE_VERSION = "0.1.0"
DEFAULT_OTLP_HTTP_BASE = "http://localhost:4318"

SIGNAL_PATHS = {
    "traces": "/v1/traces",
    "metrics": "/v1/metrics",
    "logs": "/v1/logs",
}


class TelemetryExportError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, flush=True)


def endpoint_for(signal: str) -> str:
    endpoint = os.getenv(
        f"TRACEGUARD_OTLP_{signal.upper()}_ENDPOINT",
        os.getenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT", ""),
    )
    if endpoint:
        return endpoint

    base = (
        os.getenv("TRACEGUARD_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or DEFAULT_OTLP_HTTP_BASE
    ).rstrip("/")

    if base.endswith(SIGNAL_PATHS[signal]):
        return base
    if base.endswith("/v1"):
        return f"{base}/{signal}"
    return f"{base}{SIGNAL_PATHS[signal]}"


def timeout_millis() -> int:
    raw_value = os.getenv(
        "TRACEGUARD_OTLP_TIMEOUT_SECONDS",
        os.getenv("OTEL_EXPORTER_OTLP_TIMEOUT", "10"),
    )
    try:
        seconds = float(raw_value)
    except ValueError as exc:
        raise TelemetryExportError(
            "OTLP timeout must be numeric seconds. Check "
            "TRACEGUARD_OTLP_TIMEOUT_SECONDS or OTEL_EXPORTER_OTLP_TIMEOUT."
        ) from exc

    if seconds <= 0:
        raise TelemetryExportError("OTLP timeout must be greater than zero.")
    return int(seconds * 1000)


def resource() -> Resource:
    return Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": SERVICE_VERSION,
            "deployment.environment.name": "local",
        }
    )


def require_export(
    signal_name: str,
    endpoint: str,
    result: Any,
    success_result: Any,
) -> None:
    if result is not success_result:
        raise TelemetryExportError(
            f"{signal_name} export to {endpoint} failed: exporter returned {result!s}"
        )


def create_one_span(otel_resource: Resource, run_id: str) -> tuple[Any, str]:
    span_memory_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=otel_resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_memory_exporter))

    tracer = tracer_provider.get_tracer("traceguard.gate1")
    with tracer.start_as_current_span("traceguard.gate1.connectivity") as span:
        span.set_attributes(
            {
                "traceguard.project": "TraceGuard",
                "traceguard.gate": "1A",
                "traceguard.signal": "trace",
                "traceguard.run_id": run_id,
                "agent.run_id": run_id,
                "traceguard.check": "signoz-otlp-connectivity",
            }
        )
        span.add_event(
            "gate1.telemetry.proof",
            {
                "event.kind": "connectivity",
                "event.outcome": "created-custom-span",
            },
        )
        span.set_status(Status(StatusCode.OK))
        trace_id = f"{span.get_span_context().trace_id:032x}"

    spans = span_memory_exporter.get_finished_spans()
    tracer_provider.shutdown()

    if len(spans) != 1:
        raise TelemetryExportError(f"Expected exactly one finished span, got {len(spans)}.")
    return spans, trace_id


def export_trace(
    otel_resource: Resource,
    endpoint: str,
    timeout_ms: int,
    run_id: str,
) -> str:
    spans, trace_id = create_one_span(otel_resource, run_id)
    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=timeout_ms / 1000)
    try:
        result = exporter.export(spans)
        require_export("Trace", endpoint, result, SpanExportResult.SUCCESS)
        if not exporter.force_flush(timeout_millis=timeout_ms):
            raise TelemetryExportError("Trace exporter force_flush returned false.")
    finally:
        exporter.shutdown()
    return trace_id


def collect_one_counter_increment(
    otel_resource: Resource,
    metric_exporter: OTLPMetricExporter,
    run_id: str,
) -> Any:
    reader = InMemoryMetricReader(
        preferred_temporality=getattr(metric_exporter, "_preferred_temporality", None),
        preferred_aggregation=getattr(metric_exporter, "_preferred_aggregation", None),
    )
    meter_provider = MeterProvider(resource=otel_resource, metric_readers=[reader])
    meter = meter_provider.get_meter("traceguard.gate1")
    counter = meter.create_counter(
        "traceguard.gate1.connectivity_runs",
        unit="1",
        description="One Gate 1A OTLP connectivity proof emitted by TraceGuard.",
    )
    counter.add(
        1,
        attributes={
            "traceguard.project": "TraceGuard",
            "traceguard.gate": "1A",
            "traceguard.signal": "metric",
            "traceguard.run_id": run_id,
            "agent.run_id": run_id,
        },
    )

    metrics_data = reader.get_metrics_data()
    meter_provider.shutdown()

    if metrics_data is None:
        raise TelemetryExportError("Counter was recorded, but no metric data was collected.")
    return metrics_data


def export_metric(
    otel_resource: Resource,
    endpoint: str,
    timeout_ms: int,
    run_id: str,
) -> None:
    exporter = OTLPMetricExporter(endpoint=endpoint, timeout=timeout_ms / 1000)
    try:
        metrics_data = collect_one_counter_increment(otel_resource, exporter, run_id)
        result = exporter.export(metrics_data, timeout_millis=timeout_ms)
        require_export("Metric", endpoint, result, MetricExportResult.SUCCESS)
        if not exporter.force_flush(timeout_millis=timeout_ms):
            raise TelemetryExportError("Metric exporter force_flush returned false.")
    finally:
        exporter.shutdown(timeout_millis=timeout_ms)


def emit_optional_structured_log(
    otel_resource: Resource,
    endpoint: str,
    timeout_ms: int,
    run_id: str,
) -> None:
    try:
        from opentelemetry._logs import LogRecord, SeverityNumber
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

        exporter = OTLPLogExporter(endpoint=endpoint, timeout=timeout_ms / 1000)
        logger_provider = LoggerProvider(resource=otel_resource)
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

        logger = logger_provider.get_logger("traceguard.gate1")
        logger.emit(
            LogRecord(
                timestamp=time.time_ns(),
                severity_text="INFO",
                severity_number=SeverityNumber.INFO,
                body={
                    "message": "TraceGuard Gate 1A structured log proof",
                    "project": "TraceGuard",
                    "gate": "1A",
                    "run_id": run_id,
                    "agent_run_id": run_id,
                },
                attributes={
                    "traceguard.project": "TraceGuard",
                    "traceguard.gate": "1A",
                    "traceguard.signal": "log",
                    "traceguard.run_id": run_id,
                    "agent.run_id": run_id,
                },
            )
        )
        logger_provider.force_flush(timeout_millis=timeout_ms)
        exporter.force_flush(timeout_millis=timeout_ms)
        logger_provider.shutdown()
        say("SUCCESS: emitted one optional structured log through OTLP.")
    except Exception as exc:
        print(
            "WARNING: Optional structured log export failed, but trace and metric "
            f"exports are unaffected: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def main() -> int:
    endpoints = {
        "traces": endpoint_for("traces"),
        "metrics": endpoint_for("metrics"),
        "logs": endpoint_for("logs"),
    }
    timeout_ms = timeout_millis()
    run_id = str(uuid.uuid4())
    otel_resource = resource()

    say(f"TraceGuard Gate 1A run_id={run_id}")
    say(f"service.name={SERVICE_NAME}")
    say(f"service.version={SERVICE_VERSION}")
    say(f"OTLP traces endpoint:  {endpoints['traces']}")
    say(f"OTLP metrics endpoint: {endpoints['metrics']}")
    say(f"OTLP logs endpoint:    {endpoints['logs']}")

    try:
        trace_id = export_trace(otel_resource, endpoints["traces"], timeout_ms, run_id)
        say(
            "SUCCESS: exported one custom trace "
            f"(span=traceguard.gate1.connectivity, trace_id={trace_id})."
        )

        export_metric(otel_resource, endpoints["metrics"], timeout_ms, run_id)
        say(
            "SUCCESS: exported one custom metric increment "
            "(counter=traceguard.gate1.connectivity_runs, value=1)."
        )

        emit_optional_structured_log(
            otel_resource,
            endpoints["logs"],
            timeout_ms,
            run_id,
        )
        return 0
    except TelemetryExportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
