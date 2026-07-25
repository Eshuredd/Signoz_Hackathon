from __future__ import annotations

from datetime import UTC, datetime

from gate3.models import (
    EVALUATOR_VERSION,
    RULESET_VERSION,
    EvaluationResult,
    Severity,
    RuleFinding,
    Verdict,
    equivalent_results,
    summary_from_findings,
    verdict_from_findings,
)


def test_verdict_enum_contains_pass_warn_block() -> None:
    assert {item.value for item in Verdict} == {"PASS", "WARN", "BLOCK"}


def test_severity_enum_contains_warning_and_blocking() -> None:
    assert {item.value for item in Severity} == {"WARNING", "BLOCKING"}


def test_evaluation_result_serializes_deterministically_and_ignores_evaluated_at() -> None:
    warning = RuleFinding("TG-TEL-009", "SERVICE_IDENTITY", Severity.WARNING, "msg", {"b": 2, "a": 1}, ("s1",))
    summary = summary_from_findings([warning], evaluated_rule_count=13)
    left = EvaluationResult(EVALUATOR_VERSION, RULESET_VERSION, datetime(2026, 1, 1, tzinfo=UTC), "t", Verdict.WARN, (warning,), summary, "fixture", 1)
    right = EvaluationResult(EVALUATOR_VERSION, RULESET_VERSION, datetime(2026, 1, 2, tzinfo=UTC), "t", Verdict.WARN, (warning,), summary, "fixture", 1)

    assert left.to_json() == left.to_json()
    assert '"a":1' in left.to_json()
    assert equivalent_results(left, right)


def test_finding_order_and_summary_counts_are_stable() -> None:
    blocking = RuleFinding("TG-TEL-010", "AGENT_RUN_CORRELATION", Severity.BLOCKING, "b", {}, ("s2",))
    warning = RuleFinding("TG-TEL-009", "SERVICE_IDENTITY", Severity.WARNING, "a", {}, ("s1",))
    ordered = sorted([blocking, warning], key=lambda item: item.sort_key())
    summary = summary_from_findings(ordered, evaluated_rule_count=13)

    assert [item.rule_id for item in ordered] == ["TG-TEL-009", "TG-TEL-010"]
    assert summary.blocking_count == 1
    assert summary.warning_count == 1
    assert summary.passed_rule_count == 11
    assert verdict_from_findings([]) == Verdict.PASS
    assert verdict_from_findings([warning]) == Verdict.WARN
    assert verdict_from_findings([warning, blocking]) == Verdict.BLOCK
