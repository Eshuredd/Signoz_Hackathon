from __future__ import annotations

from pathlib import Path

from gate3.evaluator import evaluate_trace
from gate3.models import RuleStatus, Verdict, equivalent_results
from gate3.rules import RULES
from gate3.trace_loader import load_trace_file


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "gate3" / "fixtures" / "trace"


def evaluate_fixture(name: str):
    return evaluate_trace(load_trace_file(FIXTURES / name))


def test_verdicts_and_one_result_per_rule() -> None:
    assert evaluate_fixture("pass_canonical_agent_trace.json").verdict == Verdict.PASS
    assert evaluate_fixture("pass_with_warnings_missing_token_usage.json").verdict == Verdict.PASS_WITH_WARNINGS
    assert evaluate_fixture("block_missing_agent_attributes.json").verdict == Verdict.BLOCK
    result = evaluate_fixture("pass_canonical_agent_trace.json")
    assert len(result.rule_results) == len(RULES)
    assert [item.sort_key() for item in result.rule_results] == sorted(item.sort_key() for item in result.rule_results)


def test_inapplicable_rules_are_explicit() -> None:
    result = evaluate_fixture("pass_no_model_spans.json")
    statuses = {item.rule_id: item.status for item in result.rule_results}
    assert statuses["TG-TEL-005"] == RuleStatus.NOT_APPLICABLE
    assert statuses["TG-TEL-006"] == RuleStatus.NOT_APPLICABLE
    assert statuses["TG-TEL-003B"] == RuleStatus.NOT_APPLICABLE
    assert statuses["TG-TEL-008"] == RuleStatus.NOT_APPLICABLE


def test_running_same_fixture_twice_is_equivalent_excluding_evaluated_at() -> None:
    assert equivalent_results(evaluate_fixture("block_tool_parent_cycle.json"), evaluate_fixture("block_tool_parent_cycle.json"))
