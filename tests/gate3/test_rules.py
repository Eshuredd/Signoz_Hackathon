from __future__ import annotations

from copy import deepcopy

from gate3.evaluator import evaluate_run_bundle, evaluate_trace
from gate3.models import RuleStatus, Verdict
from gate3.rules import RULES, RULE_BY_ID
from gate3.trace_loader import load_run_bundle_payload, load_trace_payload


TRACE_ID = "a" * 32


def span(span_id: str, name: str, parent: str | None = None, attrs: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "trace_id": TRACE_ID,
        "span_id": span_id,
        "parent_span_id": parent,
        "span_name": name,
        "start_time": "2026-07-25T08:00:00Z",
        "end_time": "2026-07-25T08:00:01Z",
        "duration_nano": 100,
        "attributes": attrs or {},
        "resource_attributes": {"service.name": "svc"},
        "service_name": "svc",
        "status": {},
    }


def root(attrs: dict[str, object] | None = None) -> dict[str, object]:
    return span("1111111111111111", "agent.run", None, attrs or {"agent.run_id": "run-1", "agent.name": "agent", "agent.status": "ok"})


def tool(parent: str = "1111111111111111", attrs: dict[str, object] | None = None) -> dict[str, object]:
    return span("2222222222222222", "tool.call", parent, {"tool.status": "ok"} if attrs is None else attrs)


def model(attrs: dict[str, object] | None = None) -> dict[str, object]:
    return span("3333333333333333", "model.call", "1111111111111111", {"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 2} if attrs is None else attrs)


def trace(spans: list[dict[str, object]]) -> object:
    return load_trace_payload({"schema_version": 1, "trace": {"trace_id": TRACE_ID, "spans": spans, "source": "fixture", "metadata": {}}})


def status(result: object, rule_id: str) -> RuleStatus:
    return {item.rule_id: item.status for item in result.rule_results}[rule_id]  # type: ignore[attr-defined]


def test_catalogue_ids_and_names() -> None:
    expected = {
        "TG-TEL-001": "AGENT_RUN_ROOT",
        "TG-TEL-002": "AGENT_RUN_REQUIRED_ATTRIBUTES",
        "TG-TEL-003A": "TOOL_PARENT_CHAIN",
        "TG-TEL-003B": "NO_TRACE_FRAGMENTATION",
        "TG-TEL-004": "TOOL_STATUS",
        "TG-TEL-005": "MODEL_IDENTITY",
        "TG-TEL-006": "TOKEN_USAGE",
        "TG-TEL-007": "TIMESTAMP_VALIDITY",
        "TG-TEL-008": "LOG_CORRELATION",
    }
    assert {rule.rule_id: rule.name for rule in RULES if rule.namespace == "TG-TEL"} == expected
    assert {rule.rule_id for rule in RULES if rule.namespace == "TG-STR"} == {"TG-STR-001", "TG-STR-002", "TG-STR-003", "TG-STR-004", "TG-STR-005"}
    assert len(RULE_BY_ID) == len(RULES)
    assert all(rule.evaluation_level and rule.severity and rule.expected for rule in RULES)


def test_canonical_trace_rules() -> None:
    assert evaluate_trace(trace([root(), tool(), model()])).verdict == Verdict.PASS
    assert status(evaluate_trace(trace([span("x", "http.request")])), "TG-TEL-001") == RuleStatus.FAILED
    assert status(evaluate_trace(trace([root({"agent.run_id": "run-1"}), tool(), model()])), "TG-TEL-002") == RuleStatus.FAILED
    assert status(evaluate_trace(trace([root(), tool("missing"), model()])), "TG-TEL-003A") == RuleStatus.FAILED
    assert status(evaluate_trace(trace([root(), tool(attrs={}), model()])), "TG-TEL-004") == RuleStatus.FAILED
    assert status(evaluate_trace(trace([root(), tool(), model({"gen_ai.usage.input_tokens": 1})])), "TG-TEL-005") == RuleStatus.FAILED
    assert evaluate_trace(trace([root(), tool(), model({"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": True, "gen_ai.usage.output_tokens": -1})])).verdict == Verdict.PASS_WITH_WARNINGS


def test_parent_cycle_and_disconnected_chain() -> None:
    cycle_parent = span("4444444444444444", "helper.step", "2222222222222222")
    assert status(evaluate_trace(trace([root(), tool("4444444444444444"), cycle_parent])), "TG-TEL-003A") == RuleStatus.FAILED
    disconnected = span("4444444444444444", "http.request", None)
    assert status(evaluate_trace(trace([root(), disconnected, tool("4444444444444444")])), "TG-TEL-003A") == RuleStatus.FAILED


def test_run_level_rules_and_logs() -> None:
    t = {"schema_version": 1, "trace": {"trace_id": TRACE_ID, "spans": [root(), tool(), model()], "source": "fixture", "metadata": {}}}
    bundle = load_run_bundle_payload({"schema_version": 1, "agent_run_id": "run-1", "traces": [t], "logs": [{"trace_id": TRACE_ID, "span_id": "1111111111111111", "attributes": {"agent.run_id": "run-1"}}]})
    result = evaluate_run_bundle(bundle)
    assert status(result, "TG-TEL-003B") == RuleStatus.PASSED
    assert status(result, "TG-TEL-008") == RuleStatus.PASSED
    bad = load_run_bundle_payload({"schema_version": 1, "agent_run_id": "run-1", "traces": [t], "logs": [{"trace_id": "b" * 32, "attributes": {"agent.run_id": "run-1"}}]})
    assert evaluate_run_bundle(bad).verdict == Verdict.PASS_WITH_WARNINGS


def test_unapproved_traceguard_attributes_do_not_affect_verdict() -> None:
    attrs = {"agent.run_id": "run-1", "agent.name": "agent", "agent.status": "ok", "traceguard.run_id": "different"}
    assert evaluate_trace(trace([root(attrs), tool(), model()])).verdict == Verdict.PASS
