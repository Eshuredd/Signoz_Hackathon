from __future__ import annotations

from gate3.evaluator import evaluate_run_bundle
from gate3.trace_loader import load_run_bundle_payload
from gate3b.bridge import build_gate3_run_bundle
from gate3b.scenarios import runtime_scenario, get_definition
from conftest import make_log, make_trace


def test_bridge_preserves_wrong_log_run_id_and_validates_schema() -> None:
    scenario = runtime_scenario(get_definition("pass_with_warnings_uncorrelated_logs"), "batch")
    trace = make_trace("a" * 32, scenario)
    logs = (
        make_log("log-1", scenario, trace.trace_id, trace.spans[0].span_id),
        make_log("log-2", scenario, trace.trace_id, trace.spans[2].span_id, run_id="wrong"),
    )
    payload = build_gate3_run_bundle(scenario.agent_run_id, (trace,), logs, {"x": "y"})
    bundle = load_run_bundle_payload(payload)
    assert bundle.logs[1].attributes["agent.run_id"] == "wrong"
    result = evaluate_run_bundle(bundle)
    statuses = {item.rule_id: item.status.value for item in result.rule_results}
    assert result.verdict.value == "PASS_WITH_WARNINGS"
    assert statuses["TG-TEL-008"] == "FAILED"
