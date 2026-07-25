from __future__ import annotations

from pathlib import Path

from gate3.evaluator import evaluate_trace
from gate3.models import Verdict, equivalent_results
from gate3.trace_loader import load_trace_file


REPO_ROOT = Path(__file__).resolve().parents[2]


def evaluate_fixture(rel_path: str):
    return evaluate_trace(load_trace_file(REPO_ROOT / "gate3" / "fixtures" / rel_path))


def test_verdict_precedence() -> None:
    assert evaluate_fixture("valid/valid_single_span.json").verdict == Verdict.PASS
    assert evaluate_fixture("warn/warn_missing_service_name.json").verdict == Verdict.WARN
    assert evaluate_fixture("block/block_missing_agent_run_id.json").verdict == Verdict.BLOCK
    assert evaluate_fixture("block/block_with_warnings.json").verdict == Verdict.BLOCK


def test_finding_and_rule_execution_order_is_deterministic() -> None:
    result = evaluate_fixture("warn/warn_multiple_conditions.json")
    assert [finding.rule_id for finding in result.findings] == sorted(finding.rule_id for finding in result.findings)
    assert result.summary.evaluated_rule_count == 13


def test_running_same_fixture_twice_is_equivalent_excluding_evaluated_at() -> None:
    assert equivalent_results(evaluate_fixture("block_child_run_id_mismatch.json".replace("_", "/", 1)) if False else evaluate_fixture("block/block_child_run_id_mismatch.json"), evaluate_fixture("block/block_child_run_id_mismatch.json"))
