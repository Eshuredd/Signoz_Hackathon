from __future__ import annotations

from datetime import UTC, datetime

from gate3.models import (
    EVALUATOR_VERSION,
    RULESET_VERSION,
    EvaluationLevel,
    EvaluationResult,
    RuleResult,
    RuleStatus,
    Severity,
    Verdict,
    equivalent_results,
    summary_from_rule_results,
    verdict_from_rule_results,
)


def result(rule_id: str, severity: Severity, status: RuleStatus) -> RuleResult:
    return RuleResult(rule_id, rule_id, rule_id.split("-")[1], severity, status, "", {}, {}, {})


def test_public_verdict_enum() -> None:
    assert {item.value for item in Verdict} == {"PASS", "PASS_WITH_WARNINGS", "BLOCK"}
    assert Verdict.PASS_WITH_WARNINGS.label == "PASS WITH WARNINGS"


def test_verdict_precedence() -> None:
    warning = result("TG-TEL-006", Severity.WARNING, RuleStatus.FAILED)
    blocking = result("TG-TEL-001", Severity.BLOCKING, RuleStatus.FAILED)
    error = result("TG-TEL-002", Severity.BLOCKING, RuleStatus.EVALUATION_ERROR)
    na = result("TG-TEL-008", Severity.WARNING, RuleStatus.NOT_APPLICABLE)
    assert verdict_from_rule_results([na]) == Verdict.PASS
    assert verdict_from_rule_results([warning]) == Verdict.PASS_WITH_WARNINGS
    assert verdict_from_rule_results([warning, blocking]) == Verdict.BLOCK
    assert verdict_from_rule_results([error]) == Verdict.BLOCK


def test_result_serializes_deterministically_and_ignores_evaluated_at() -> None:
    rule = result("TG-TEL-006", Severity.WARNING, RuleStatus.FAILED)
    summary = summary_from_rule_results([rule])
    left = EvaluationResult(EVALUATOR_VERSION, RULESET_VERSION, datetime(2026, 1, 1, tzinfo=UTC), EvaluationLevel.TRACE, "run-1", ("t",), Verdict.PASS_WITH_WARNINGS, (rule,), summary, "fixture", 1)
    right = EvaluationResult(EVALUATOR_VERSION, RULESET_VERSION, datetime(2026, 1, 2, tzinfo=UTC), EvaluationLevel.TRACE, "run-1", ("t",), Verdict.PASS_WITH_WARNINGS, (rule,), summary, "fixture", 1)
    assert left.to_dict()["verdict"] != "WARN"
    assert equivalent_results(left, right)
