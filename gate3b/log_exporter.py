from __future__ import annotations

import logging
from typing import Callable

from opentelemetry.sdk.resources import Resource

from .config import Gate3BConfig
from .models import LOG_ID_ATTR, TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, LogEmissionResult, RuntimeScenario, TraceEmissionResult, now_iso
from .otel_log_compat import Gate3BLogExportError, InMemoryLogExporter, LoggerProvider, LoggingHandler, LogExportResult, LogRecordExportResult, OTLPLogExporter, SimpleLogRecordProcessor


def emit_logs(
    scenario: RuntimeScenario,
    config: Gate3BConfig,
    trace_emission: TraceEmissionResult,
    *,
    otlp_exporter_factory: Callable[..., object] = OTLPLogExporter,
) -> LogEmissionResult:
    records, manifest = _create_memory_logs(scenario, config, trace_emission)
    _verify_memory_logs(scenario, records, manifest)
    if not records:
        return manifest
    exporter = otlp_exporter_factory(endpoint=config.log_otlp_endpoint, timeout=config.otlp_timeout_seconds)
    try:
        result = exporter.export(records)  # type: ignore[attr-defined]
        success_values = {LogExportResult.SUCCESS, LogRecordExportResult.SUCCESS}
        if result not in success_values:
            raise Gate3BLogExportError("OTLP log export did not return LogExportResult.SUCCESS.")
        force_flush = getattr(exporter, "force_flush", None)
        if force_flush is not None and not force_flush(timeout_millis=int(config.otlp_timeout_seconds * 1000)):
            raise Gate3BLogExportError("OTLP log exporter force_flush did not succeed.")
    finally:
        exporter.shutdown()  # type: ignore[attr-defined]
    return manifest


def _create_memory_logs(scenario: RuntimeScenario, config: Gate3BConfig, trace_emission: TraceEmissionResult):
    provider = LoggerProvider(resource=Resource.create({"service.name": config.service_name}))
    memory_exporter = InMemoryLogExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(memory_exporter))
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    logger = logging.getLogger(f"traceguard.gate3b.{scenario.scenario_id}")
    logger.handlers = []
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    trace_ids = trace_emission.emitted_trace_ids
    expected_agent_run_ids: dict[str, str] = {}
    expected_trace_ids: dict[str, str] = {}
    expected_span_ids: dict[str, str] = {}
    bodies: dict[str, str] = {}
    try:
        for index, spec in enumerate(scenario.definition.log_plan):
            log_id = scenario.log_ids[index]
            trace_id = trace_ids[min(index, len(trace_ids) - 1)]
            span_id = trace_emission.span_ids_by_trace_id_and_name[trace_id][spec.span_name]
            run_id = scenario.agent_run_id if spec.agent_run_id_mode == "match" else f"{scenario.agent_run_id}-mismatch"
            body = f"{spec.body}: {scenario.name}:{spec.name}"
            attrs = {
                LOG_ID_ATTR: log_id,
                TRACE_BATCH_ATTR: scenario.batch_id,
                TRACE_SCENARIO_ATTR: scenario.scenario_id,
                TRACE_SCENARIO_NAME_ATTR: scenario.name,
                "agent.run_id": run_id,
                "trace_id": trace_id,
                "span_id": span_id,
            }
            logger.info(body, extra=attrs)
            expected_agent_run_ids[log_id] = run_id
            expected_trace_ids[log_id] = trace_id
            expected_span_ids[log_id] = span_id
            bodies[log_id] = body
        records = tuple(memory_exporter.get_finished_logs())
        manifest = LogEmissionResult(scenario.name, scenario.log_ids, expected_agent_run_ids, expected_trace_ids, expected_span_ids, bodies, now_iso())
        return records, manifest
    finally:
        logger.removeHandler(handler)
        try:
            provider.shutdown()
        except Exception:
            pass


def _verify_memory_logs(scenario: RuntimeScenario, records: tuple[object, ...], manifest: LogEmissionResult) -> None:
    if len(records) != scenario.definition.expected_log_count:
        raise Gate3BLogExportError("Local log creation produced the wrong number of logs.")
    seen: set[str] = set()
    for record in records:
        attrs = _attrs(record)
        log_id = attrs.get(LOG_ID_ATTR)
        if not isinstance(log_id, str) or not log_id:
            raise Gate3BLogExportError("Every log must contain a unique Gate 3B log ID.")
        if log_id in seen:
            raise Gate3BLogExportError("Gate 3B log IDs must be unique.")
        seen.add(log_id)
        for key in (TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR):
            if attrs.get(key) not in {scenario.batch_id, scenario.scenario_id, scenario.name}:
                raise Gate3BLogExportError(f"Log {log_id} missing expected scenario correlation.")
        if attrs.get("agent.run_id") != manifest.expected_agent_run_ids[log_id]:
            raise Gate3BLogExportError(f"Log {log_id} agent.run_id changed before export.")
        if attrs.get("trace_id") != manifest.expected_trace_ids[log_id] or attrs.get("span_id") != manifest.expected_span_ids[log_id]:
            raise Gate3BLogExportError(f"Log {log_id} trace/span correlation changed before export.")
        if not manifest.bodies.get(log_id):
            raise Gate3BLogExportError(f"Log {log_id} body must be synthetic and non-empty.")


def _attrs(record: object) -> dict[str, object]:
    log_record = getattr(record, "log_record", record)
    attrs = getattr(log_record, "attributes", None)
    return dict(attrs or {})
