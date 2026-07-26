from __future__ import annotations

from .models import LOG_ID_ATTR, TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, LogEmissionResult, RetrievedLog, RuntimeScenario, TraceEmissionResult, VerificationResult


def verify_preservation(
    scenario: RuntimeScenario,
    trace_emission: TraceEmissionResult,
    retrieved_traces: tuple[object, ...],
    log_emission: LogEmissionResult,
    retrieved_logs: tuple[RetrievedLog, ...],
) -> VerificationResult:
    errors: list[str] = []
    traces_by_id = {trace.trace_id: trace for trace in retrieved_traces}
    if set(traces_by_id) != set(trace_emission.emitted_trace_ids):
        errors.append("retrieved trace IDs do not exactly match emitted trace IDs")
    for trace_id, trace in traces_by_id.items():
        spans_by_name = {span.span_name: span for span in trace.spans}
        if set(spans_by_name) != {"agent.run", "tool.call", "model.call"} or len(trace.spans) != 3:
            errors.append(f"{trace_id} does not contain exactly agent.run/tool.call/model.call")
            continue
        expected_spans = trace_emission.span_ids_by_trace_id_and_name.get(trace_id, {})
        if {name: span.span_id for name, span in spans_by_name.items()} != expected_spans:
            errors.append(f"{trace_id} span IDs changed")
        root_id = spans_by_name["agent.run"].span_id
        if spans_by_name["tool.call"].parent_span_id != root_id or spans_by_name["model.call"].parent_span_id != root_id:
            errors.append(f"{trace_id} parent relationships changed")
        for span in trace.spans:
            attrs = span.attributes
            if attrs.get(TRACE_BATCH_ATTR) != scenario.batch_id or attrs.get(TRACE_SCENARIO_ATTR) != scenario.scenario_id or attrs.get(TRACE_SCENARIO_NAME_ATTR) != scenario.name:
                errors.append(f"{trace_id} span {span.span_id} missing Gate 3B correlation")

    logs_by_id = {log.log_id: log for log in retrieved_logs}
    if set(logs_by_id) != set(log_emission.log_ids):
        errors.append("retrieved log IDs do not exactly match emitted log IDs")
    for log_id, log in logs_by_id.items():
        if log.attributes.get(LOG_ID_ATTR) != log_id or log.attributes.get(TRACE_SCENARIO_ATTR) != scenario.scenario_id:
            errors.append(f"{log_id} missing Gate 3B log correlation")
        if log.trace_id != log_emission.expected_trace_ids.get(log_id):
            errors.append(f"{log_id} trace_id changed")
        if log.span_id != log_emission.expected_span_ids.get(log_id):
            errors.append(f"{log_id} span_id changed")
        if log.attributes.get("agent.run_id") != log_emission.expected_agent_run_ids.get(log_id):
            errors.append(f"{log_id} agent.run_id changed")
        if log.body != log_emission.bodies.get(log_id):
            errors.append(f"{log_id} body changed")
    return VerificationResult(
        trace_preservation_result=not any("trace" in error or "span" in error or "parent" in error for error in errors),
        log_preservation_result=not any("log" in error or "agent.run_id" in error or "body" in error for error in errors),
        errors=tuple(errors),
    )

