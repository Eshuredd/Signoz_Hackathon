from __future__ import annotations

from pathlib import Path

import pytest

from gate3.cli import EXPECTATION_PATH, load_expectations
from gate3.evaluator import evaluate_trace
from gate3.models import equivalent_results
from gate3.rules import RULE_BY_ID
from gate3.trace_loader import load_trace_file


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "gate3" / "fixtures"
FIXTURE_REL_PATHS = sorted(path.relative_to(FIXTURES_DIR).as_posix() for path in FIXTURES_DIR.rglob("*.json"))


def test_every_fixture_and_expectation_match_and_rule_ids_exist() -> None:
    expectations = load_expectations(FIXTURES_DIR, EXPECTATION_PATH)
    fixture_paths = sorted(path.relative_to(FIXTURES_DIR).as_posix() for path in FIXTURES_DIR.rglob("*.json"))

    assert sorted(expectations) == fixture_paths
    for rel_path, expected in expectations.items():
        assert set(expected["rule_ids"]).issubset(RULE_BY_ID)
        result = evaluate_trace(load_trace_file(FIXTURES_DIR / rel_path))
        assert result.verdict.value == expected["verdict"]
        assert sorted({finding.rule_id for finding in result.findings}) == expected["rule_ids"]


def test_all_fixture_runs_are_deterministic() -> None:
    expectations = load_expectations(FIXTURES_DIR, EXPECTATION_PATH)
    for rel_path in sorted(expectations):
        first = evaluate_trace(load_trace_file(FIXTURES_DIR / rel_path))
        second = evaluate_trace(load_trace_file(FIXTURES_DIR / rel_path))
        assert equivalent_results(first, second)


@pytest.mark.parametrize("rel_path", FIXTURE_REL_PATHS)
def test_each_fixture_matches_its_independent_expectation(rel_path: str) -> None:
    expectations = load_expectations(FIXTURES_DIR, EXPECTATION_PATH)
    result = evaluate_trace(load_trace_file(FIXTURES_DIR / rel_path))

    assert result.verdict.value == expectations[rel_path]["verdict"]
    assert sorted({finding.rule_id for finding in result.findings}) == expectations[rel_path]["rule_ids"]
