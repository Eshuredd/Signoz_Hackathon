from __future__ import annotations

from datetime import datetime

from .models import LOG_ID_ATTR, TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, LogEmissionResult, LogPreservationResult, RetrievedLog, RuntimeScenario, TraceEmissionResult, TracePreservationResult, VerificationResult


CANONICAL_BY_SPAN = {
    "agent.run": ("agent.run_id", "agent.name", "agent.status"),
    "tool.call": ("tool.status",),
    "model.call": ("gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"),
}


def verify_preservation(
    scenario: RuntimeScenario,
    trace_emission: TraceEmissionResult,
    retrieved_traces: tuple[object, ...],
    log_emission: LogEmissionResult,
    retrieved_logs: tuple[RetrievedLog, ...],
) -> VerificationResult:
    trace_details = _verify_traces(scenario, trace_emission, retrieved_traces)
    log_details = _verify_logs(scenario, trace_emission, retrieved_traces, log_emission, retrieved_logs)
    errors = list(trace_details.errors) + list(log_details.errors)
    return VerificationResult(
        trace_preservation_result=trace_details.passed,
        log_preservation_result=log_details.passed,
        trace_details=trace_details,
        log_details=log_details,
        errors=tuple(errors),
    )


def _verify_traces(scenario: RuntimeScenario, emission: TraceEmissionResult, retrieved_traces: tuple[object, ...]) -> TracePreservationResult:
    errors: list[str] = []
    trace_ids = [trace.trace_id for trace in retrieved_traces]
    duplicate_traces = len(trace_ids) != len(set(trace_ids))
    traces_by_id = {trace.trace_id: trace for trace in retrieved_traces}
    expected_trace_ids = set(emission.emitted_trace_ids)
    trace_ids_match = set(trace_ids) == expected_trace_ids and not duplicate_traces
    if not trace_ids_match:
        errors.append("retrieved trace IDs do not exactly match emitted trace IDs")
    if duplicate_traces:
        errors.append("duplicate retrieved trace object exists")

    span_count_match = True
    span_names_match = True
    span_ids_match = True
    parent_relationships_match = True
    canonical_attributes_match = True
    run_id_preserved = True
    scenario_correlation_match = True
    service_identity_preserved = True
    timing_preserved = True
    cross_trace_span_ids: dict[str, str] = {}
    root_run_ids: set[object] = set()

    for trace_id, trace in traces_by_id.items():
        spans = list(trace.spans)
        if len(spans) != 3:
            span_count_match = False
            errors.append(f"{trace_id} does not contain exactly three spans")
        names = [span.span_name for span in spans]
        if set(names) != {"agent.run", "tool.call", "model.call"} or len(names) != len(set(names)):
            span_names_match = False
            errors.append(f"{trace_id} span names changed or duplicated")
        spans_by_name = {span.span_name: span for span in spans}
        expected_spans = emission.span_ids_by_trace_id_and_name.get(trace_id, {})
        actual_spans = {name: span.span_id for name, span in spans_by_name.items()}
        span_ids = [span.span_id for span in spans]
        if actual_spans != expected_spans or any(not item for item in span_ids) or len(span_ids) != len(set(span_ids)):
            span_ids_match = False
            errors.append(f"{trace_id} span IDs changed, repeated, or became empty")
        for span in spans:
            if span.trace_id != trace_id:
                trace_ids_match = False
                errors.append(f"{trace_id} span {span.span_id} has mismatched trace_id")
            if span.span_id in cross_trace_span_ids and cross_trace_span_ids[span.span_id] != trace_id:
                span_ids_match = False
                errors.append("span ID reused across traces")
            cross_trace_span_ids[span.span_id] = trace_id
        if {"agent.run", "tool.call", "model.call"} <= set(spans_by_name):
            root = spans_by_name["agent.run"]
            root_run_ids.add(root.attributes.get("agent.run_id"))
            actual_parent_map = {name: _parent_id(span.parent_span_id) for name, span in spans_by_name.items()}
            expected_parent_map = emission.parent_span_ids_by_trace_id_and_name.get(trace_id, {})
            expected_parent_map = {name: _parent_id(parent) for name, parent in expected_parent_map.items()}
            if _parent_id(root.parent_span_id) is not None or actual_parent_map != expected_parent_map:
                parent_relationships_match = False
                errors.append(f"{trace_id} parent relationships changed")
            for child in (spans_by_name["tool.call"], spans_by_name["model.call"]):
                if _parent_id(child.parent_span_id) != root.span_id:
                    parent_relationships_match = False
                if _parent_id(child.parent_span_id) and _parent_id(child.parent_span_id) not in {span.span_id for span in spans}:
                    parent_relationships_match = False
                    errors.append("cross-trace or unknown parent reference detected")
            if root.attributes.get("agent.run_id") != emission.agent_run_id:
                run_id_preserved = False
                errors.append(f"{trace_id} agent.run_id changed")
            expected_attrs = emission.expected_attributes_by_trace_id_and_name.get(trace_id, {})
            for name, keys in CANONICAL_BY_SPAN.items():
                span = spans_by_name.get(name)
                if not span:
                    continue
                for key in keys:
                    if span.attributes.get(key) != expected_attrs.get(name, {}).get(key):
                        canonical_attributes_match = False
                        errors.append(f"{trace_id} {name} canonical attribute {key} changed")
        for span in spans:
            attrs = span.attributes
            if attrs.get(TRACE_BATCH_ATTR) != scenario.batch_id or attrs.get(TRACE_SCENARIO_ATTR) != scenario.scenario_id or attrs.get(TRACE_SCENARIO_NAME_ATTR) != scenario.name:
                scenario_correlation_match = False
                errors.append(f"{trace_id} span {span.span_id} missing Gate 3B correlation")
            service_name = span.service_name or span.resource_attributes.get("service.name")
            if service_name != "svc" and not service_name:
                service_identity_preserved = False
                errors.append(f"{trace_id} span {span.span_id} missing service identity")
            if not _timing_ok(span.start_time, span.end_time, span.duration_nano):
                timing_preserved = False
                errors.append(f"{trace_id} span {span.span_id} has invalid timing")

    fragmentation_preserved = True
    if scenario.name == "block_fragmented_run":
        fragmentation_preserved = len(expected_trace_ids) == 2 and len(traces_by_id) == 2 and root_run_ids == {scenario.agent_run_id} and parent_relationships_match
        if not fragmentation_preserved:
            errors.append("fragmented run preservation failed")

    return TracePreservationResult(trace_ids_match, span_count_match, span_names_match, span_ids_match, parent_relationships_match, canonical_attributes_match, run_id_preserved, fragmentation_preserved, scenario_correlation_match, service_identity_preserved, timing_preserved, tuple(errors))


def _verify_logs(scenario: RuntimeScenario, trace_emission: TraceEmissionResult, retrieved_traces: tuple[object, ...], emission: LogEmissionResult, retrieved_logs: tuple[RetrievedLog, ...]) -> LogPreservationResult:
    errors: list[str] = []
    log_ids = [log.log_id for log in retrieved_logs]
    expected = set(emission.log_ids)
    log_ids_match = set(log_ids) == expected and len(log_ids) == len(set(log_ids))
    if not log_ids_match:
        errors.append("retrieved log IDs do not exactly match emitted log IDs")
    trace_span_ids = {trace.trace_id: {span.span_id for span in trace.spans} for trace in retrieved_traces}
    scenario_correlation_match = True
    trace_span_correlation_match = True
    agent_run_id_preserved = True
    intentional_mismatch_preserved = True
    body_preserved = True
    timestamp_preserved = True
    service_identity_preserved = True
    resource_attributes_preserved = True
    for log in retrieved_logs:
        attrs = log.attributes
        if attrs.get(LOG_ID_ATTR) != log.log_id or attrs.get(TRACE_BATCH_ATTR) != scenario.batch_id or attrs.get(TRACE_SCENARIO_ATTR) != scenario.scenario_id or attrs.get(TRACE_SCENARIO_NAME_ATTR) != scenario.name:
            scenario_correlation_match = False
            errors.append(f"{log.log_id} missing Gate 3B log correlation")
        if log.trace_id != emission.expected_trace_ids.get(log.log_id) or log.span_id != emission.expected_span_ids.get(log.log_id):
            trace_span_correlation_match = False
            errors.append(f"{log.log_id} trace/span IDs changed")
        if not log.trace_id or not log.span_id or log.trace_id not in trace_span_ids or log.span_id not in trace_span_ids.get(log.trace_id, set()):
            trace_span_correlation_match = False
            errors.append(f"{log.log_id} trace/span membership is invalid")
        expected_run_id = emission.expected_agent_run_ids.get(log.log_id)
        if attrs.get("agent.run_id") != expected_run_id:
            agent_run_id_preserved = False
            errors.append(f"{log.log_id} agent.run_id changed")
        if expected_run_id and expected_run_id.endswith("-mismatch"):
            if attrs.get("agent.run_id") == scenario.agent_run_id:
                intentional_mismatch_preserved = False
                errors.append(f"{log.log_id} intentional run-id mismatch was repaired")
        if log.body != emission.bodies.get(log.log_id) or log.body in {None, ""}:
            body_preserved = False
            errors.append(f"{log.log_id} body changed or is empty")
        if not _timestamp_string_ok(log.timestamp):
            timestamp_preserved = False
            errors.append(f"{log.log_id} timestamp missing or invalid")
        service_name = log.service_name or log.resource_attributes.get("service.name")
        if not service_name:
            service_identity_preserved = False
            errors.append(f"{log.log_id} missing service identity")
        if not log.resource_attributes or "service.name" not in log.resource_attributes:
            resource_attributes_preserved = False
            errors.append(f"{log.log_id} missing resource service.name")
    return LogPreservationResult(log_ids_match, scenario_correlation_match, trace_span_correlation_match, agent_run_id_preserved, intentional_mismatch_preserved, body_preserved, timestamp_preserved, service_identity_preserved, resource_attributes_preserved, tuple(errors))


def _timing_ok(start: object, end: object, duration: object) -> bool:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    if start.tzinfo is None or end.tzinfo is None:
        return False
    if end < start:
        return False
    return isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0


def _timestamp_string_ok(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _parent_id(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)
