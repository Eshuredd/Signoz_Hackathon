from __future__ import annotations

import pytest
from opentelemetry.sdk._logs.export import LogExportResult, LogRecordExportResult
from opentelemetry.sdk.trace.export import SpanExportResult

from gate3b.log_exporter import Gate3BLogExportError, emit_logs
from gate3b.trace_exporter import Gate3BTraceExportError, emit_traces


class TraceExporter:
    instances: list["TraceExporter"] = []

    def __init__(self, **kwargs: object) -> None:
        self.shutdown_called = False
        self.exported = ()
        TraceExporter.instances.append(self)

    def export(self, spans):
        self.exported = tuple(spans)
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True


class FailingTraceExporter(TraceExporter):
    def export(self, spans):
        super().export(spans)
        return SpanExportResult.FAILURE


class LogExporter:
    instances: list["LogExporter"] = []

    def __init__(self, **kwargs: object) -> None:
        self.shutdown_called = False
        self.exported = ()
        LogExporter.instances.append(self)

    def export(self, logs):
        self.exported = tuple(logs)
        return LogExportResult.SUCCESS

    def force_flush(self, timeout_millis: int) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True


class FailingLogExporter(LogExporter):
    def export(self, logs):
        super().export(logs)
        return LogExportResult.FAILURE


class RecordResultLogExporter(LogExporter):
    def export(self, logs):
        self.exported = tuple(logs)
        return LogRecordExportResult.SUCCESS


def test_trace_exporter_shapes_and_shutdown(config, scenario) -> None:
    result = emit_traces(scenario, config, otlp_exporter_factory=TraceExporter)
    assert len(result.emitted_trace_ids) == 1
    trace_id = result.emitted_trace_ids[0]
    assert set(result.span_ids_by_trace_id_and_name[trace_id]) == {"agent.run", "tool.call", "model.call"}
    assert TraceExporter.instances[-1].shutdown_called is True
    with pytest.raises(Gate3BTraceExportError):
        emit_traces(scenario, config, otlp_exporter_factory=FailingTraceExporter)


def test_log_exporter_preserves_mismatched_run_id(config) -> None:
    from gate3b.scenarios import get_definition, runtime_scenario

    scenario = runtime_scenario(get_definition("pass_with_warnings_uncorrelated_logs"), "batch")
    trace_result = emit_traces(scenario, config, otlp_exporter_factory=TraceExporter)
    log_result = emit_logs(scenario, config, trace_result, otlp_exporter_factory=LogExporter)
    assert len(log_result.log_ids) == 2
    assert any(value.endswith("-mismatch") for value in log_result.expected_agent_run_ids.values())
    assert LogExporter.instances[-1].shutdown_called is True
    assert emit_logs(scenario, config, trace_result, otlp_exporter_factory=RecordResultLogExporter).exported is True
    with pytest.raises(Gate3BLogExportError):
        emit_logs(scenario, config, trace_result, otlp_exporter_factory=FailingLogExporter)
