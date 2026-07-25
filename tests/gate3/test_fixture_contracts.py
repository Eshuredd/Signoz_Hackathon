from __future__ import annotations

from pathlib import Path

import pytest

from gate3.cli import RUN_EXPECTATION_PATH, TRACE_EXPECTATION_PATH, ExpectationError, actual_expectation, load_expectations
from gate3.evaluator import evaluate_run_bundle, evaluate_trace
from gate3.models import equivalent_results
from gate3.rules import RULE_BY_ID
from gate3.trace_loader import load_run_bundle_file, load_trace_file


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_FIXTURES = REPO_ROOT / "gate3" / "fixtures" / "trace"
RUN_FIXTURES = REPO_ROOT / "gate3" / "fixtures" / "run"


def test_every_trace_fixture_has_complete_expectation() -> None:
    expectations = load_expectations(TRACE_FIXTURES, TRACE_EXPECTATION_PATH)
    fixture_paths = sorted(path.relative_to(TRACE_FIXTURES).as_posix() for path in TRACE_FIXTURES.rglob("*.json"))
    assert sorted(expectations) == fixture_paths
    for rel_path, expected in expectations.items():
        assert set(expected["rule_statuses"]) == set(RULE_BY_ID)
        assert actual_expectation(evaluate_trace(load_trace_file(TRACE_FIXTURES / rel_path)).to_dict()) == expected


def test_every_run_fixture_has_complete_expectation() -> None:
    expectations = load_expectations(RUN_FIXTURES, RUN_EXPECTATION_PATH)
    fixture_paths = sorted(path.relative_to(RUN_FIXTURES).as_posix() for path in RUN_FIXTURES.rglob("*.json"))
    assert sorted(expectations) == fixture_paths
    for rel_path, expected in expectations.items():
        assert set(expected["rule_statuses"]) == set(RULE_BY_ID)
        assert actual_expectation(evaluate_run_bundle(load_run_bundle_file(RUN_FIXTURES / rel_path)).to_dict()) == expected


def test_all_fixture_runs_are_deterministic() -> None:
    for path in sorted(TRACE_FIXTURES.rglob("*.json")):
        assert equivalent_results(evaluate_trace(load_trace_file(path)), evaluate_trace(load_trace_file(path)))
    for path in sorted(RUN_FIXTURES.rglob("*.json")):
        assert equivalent_results(evaluate_run_bundle(load_run_bundle_file(path)), evaluate_run_bundle(load_run_bundle_file(path)))


def test_expectation_manifest_rejects_missing_statuses(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "x.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "expectations.json"
    manifest.write_text('{"schema_version":1,"fixtures":{"x.json":{"verdict":"PASS","rule_statuses":{}}}}', encoding="utf-8")
    with pytest.raises(ExpectationError, match="missing rule status"):
        load_expectations(fixtures_dir, manifest)
