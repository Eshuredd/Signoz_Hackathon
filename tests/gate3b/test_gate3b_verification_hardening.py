from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from gate3b.models import LogEmissionResult, TraceEmissionResult
from gate3b.verification import verify_preservation
from conftest import make_log, make_trace


def emission_for(scenario, trace_id: str = "a" * 32, root: str = "1" * 16, tool: str = "2" * 16, model: str = "3" * 16) -> TraceEmissionResult:
    base = {
        "traceguard.gate3b_batch_id": scenario.batch_id,
        "traceguard.gate3b_scenario_id": scenario.scenario_id,
        "traceguard.gate3b_scenario_name": scenario.name,
    }
    return TraceEmissionResult(
        scenario.name,
        scenario.agent_run_id,
        "svc",
        (trace_id,),
        {trace_id: root},
        {trace_id: {"agent.run": root, "tool.call": tool, "model.call": model}},
        {trace_id: {"agent.run": None, "tool.call": root, "model.call": root}},
        {
            trace_id: {
                "agent.run": base | {"agent.run_id": scenario.agent_run_id, "agent.name": "traceguard-gate3b", "agent.status": "success"},
                "tool.call": base | {"tool.status": "success"},
                "model.call": base | {"gen_ai.request.model": "gpt-gate3b", "gen_ai.usage.input_tokens": 7, "gen_ai.usage.output_tokens": 11},
            }
        },
        "now",
    )


def logs_for(scenario, trace_id: str = "a" * 32, root: str = "1" * 16, tool: str = "2" * 16) -> LogEmissionResult:
    ids = scenario.log_ids
    return LogEmissionResult(
        scenario.name,
        "svc",
        ids,
        {ids[0]: scenario.agent_run_id, ids[1]: scenario.agent_run_id},
        {ids[0]: trace_id, ids[1]: trace_id},
        {ids[0]: root, ids[1]: tool},
        {ids[0]: "body-0", ids[1]: "body-1"},
        "now",
    )


def test_fully_preserved_trace_and_logs_pass(scenario) -> None:
    trace_id = "a" * 32
    trace = make_trace(trace_id, scenario)
    trace_emission = emission_for(scenario, trace_id)
    log_emission = logs_for(scenario, trace_id)
    logs = (
        make_log(scenario.log_ids[0], scenario, trace_id, "1" * 16, body="body-0"),
        make_log(scenario.log_ids[1], scenario, trace_id, "2" * 16, body="body-1"),
    )
    result = verify_preservation(scenario, trace_emission, (trace,), log_emission, logs)
    assert result.passed is True
    assert result.trace_details is not None and result.trace_details.timing_preserved is True
    assert result.log_details is not None and result.log_details.trace_span_correlation_match is True


def test_trace_parent_canonical_service_and_timing_failures_are_reported(scenario) -> None:
    trace_id = "a" * 32
    trace = make_trace(trace_id, scenario)
    trace.spans[0] = replace(trace.spans[0], parent_span_id="9" * 16, service_name=None, resource_attributes={}, end_time=trace.spans[0].start_time - timedelta(seconds=1))
    trace.spans[2] = replace(trace.spans[2], attributes=trace.spans[2].attributes | {"gen_ai.usage.input_tokens": 99})
    result = verify_preservation(scenario, emission_for(scenario, trace_id), (trace,), logs_for(scenario, trace_id), ())
    assert result.trace_details is not None
    assert result.trace_details.parent_relationships_match is False
    assert result.trace_details.canonical_attributes_match is False
    assert result.trace_details.service_identity_preserved is False
    assert result.trace_details.timing_preserved is False


def test_exact_service_names_are_required(scenario) -> None:
    trace_id = "a" * 32
    trace_emission = emission_for(scenario, trace_id)
    log_emission = logs_for(scenario, trace_id)
    good_trace = make_trace(trace_id, scenario, service_name="svc")
    good_logs = (
        make_log(scenario.log_ids[0], scenario, trace_id, "1" * 16, body="body-0"),
        make_log(scenario.log_ids[1], scenario, trace_id, "2" * 16, body="body-1"),
    )
    assert verify_preservation(scenario, trace_emission, (good_trace,), log_emission, good_logs).passed is True

    bad_trace = make_trace(trace_id, scenario, service_name="other")
    trace_result = verify_preservation(scenario, trace_emission, (bad_trace,), log_emission, good_logs)
    assert trace_result.trace_details is not None
    assert trace_result.trace_details.service_identity_preserved is False

    bad_log = replace(good_logs[0], service_name="other")
    log_result = verify_preservation(scenario, trace_emission, (good_trace,), log_emission, (bad_log, good_logs[1]))
    assert log_result.log_details is not None
    assert log_result.log_details.service_identity_preserved is False

    bad_resource = replace(good_logs[0], resource_attributes={"service.name": "other"})
    resource_result = verify_preservation(scenario, trace_emission, (good_trace,), log_emission, (bad_resource, good_logs[1]))
    assert resource_result.log_details is not None
    assert resource_result.log_details.resource_attributes_preserved is False


def test_service_identity_values_serialize(scenario) -> None:
    assert emission_for(scenario).to_dict()["service_name"] == "svc"
    assert logs_for(scenario).to_dict()["service_name"] == "svc"


def test_log_intentional_mismatch_must_not_be_repaired(config) -> None:
    from gate3b.scenarios import get_definition, runtime_scenario

    scenario = runtime_scenario(get_definition("pass_with_warnings_uncorrelated_logs"), "batch")
    trace_id = "b" * 32
    trace = make_trace(trace_id, scenario)
    trace_emission = emission_for(scenario, trace_id)
    ids = scenario.log_ids
    log_emission = LogEmissionResult(
        scenario.name,
        "svc",
        ids,
        {ids[0]: scenario.agent_run_id, ids[1]: f"{scenario.agent_run_id}-mismatch"},
        {ids[0]: trace_id, ids[1]: trace_id},
        {ids[0]: "1" * 16, ids[1]: "3" * 16},
        {ids[0]: "body-0", ids[1]: "body-1"},
        "now",
    )
    repaired = (
        make_log(ids[0], scenario, trace_id, "1" * 16, run_id=scenario.agent_run_id, body="body-0"),
        make_log(ids[1], scenario, trace_id, "3" * 16, run_id=scenario.agent_run_id, body="body-1"),
    )
    result = verify_preservation(scenario, trace_emission, (trace,), log_emission, repaired)
    assert result.log_details is not None
    assert result.log_details.intentional_mismatch_preserved is False
    assert result.log_details.agent_run_id_preserved is False
