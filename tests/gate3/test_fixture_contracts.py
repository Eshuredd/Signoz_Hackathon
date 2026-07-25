from __future__ import annotations

from pathlib import Path

import pytest

from gate3.cli import EXPECTATION_PATH, ExpectationError, load_expectations
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


def write_manifest(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def one_fixture_dir(tmp_path: Path) -> Path:
    fixtures_dir = tmp_path / "fixtures"
    fixture_dir = fixtures_dir / "valid"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "valid_single_span.json").write_text("{}", encoding="utf-8")
    return fixtures_dir


@pytest.mark.parametrize(
    "manifest_text,match",
    [
        (
            """
            {
              "schema_version": 1,
              "schema_version": 1,
              "fixtures": {}
            }
            """,
            "schema_version",
        ),
        (
            """
            {
              "schema_version": 1,
              "fixtures": {
                "valid/valid_single_span.json": {"verdict": "PASS", "rule_ids": []},
                "valid/valid_single_span.json": {"verdict": "BLOCK", "rule_ids": ["TG-TEL-001"]}
              }
            }
            """,
            "valid/valid_single_span.json",
        ),
        (
            """
            {
              "schema_version": 1,
              "fixtures": {
                "valid/valid_single_span.json": {
                  "verdict": "PASS",
                  "verdict": "BLOCK",
                  "rule_ids": []
                }
              }
            }
            """,
            "verdict",
        ),
        (
            """
            {
              "schema_version": 1,
              "fixtures": {
                "valid/valid_single_span.json": {
                  "verdict": "PASS",
                  "rule_ids": [],
                  "rule_ids": ["TG-TEL-001"]
                }
              }
            }
            """,
            "rule_ids",
        ),
    ],
)
def test_expectation_manifest_rejects_duplicate_json_object_keys(
    tmp_path: Path,
    manifest_text: str,
    match: str,
) -> None:
    fixtures_dir = one_fixture_dir(tmp_path)
    manifest_path = write_manifest(tmp_path / "expectations.json", manifest_text)

    with pytest.raises(ExpectationError, match=match):
        load_expectations(fixtures_dir, manifest_path)


def test_expectation_manifest_rejects_duplicate_rule_id_values(tmp_path: Path) -> None:
    fixtures_dir = one_fixture_dir(tmp_path)
    manifest_path = write_manifest(
        tmp_path / "expectations.json",
        """
        {
          "schema_version": 1,
          "fixtures": {
            "valid/valid_single_span.json": {
              "verdict": "BLOCK",
              "rule_ids": ["TG-TEL-001", "TG-TEL-001"]
            }
          }
        }
        """,
    )

    with pytest.raises(ExpectationError, match="duplicate rule IDs"):
        load_expectations(fixtures_dir, manifest_path)


@pytest.mark.parametrize(
    "schema_version_json",
    ["true", "false", "1.0", '"1"', "null", "[]", "{}"],
)
def test_expectation_manifest_rejects_schema_version_invalid_types(
    tmp_path: Path,
    schema_version_json: str,
) -> None:
    fixtures_dir = one_fixture_dir(tmp_path)
    manifest_path = write_manifest(
        tmp_path / "expectations.json",
        f"""
        {{
          "schema_version": {schema_version_json},
          "fixtures": {{}}
        }}
        """,
    )

    with pytest.raises(ExpectationError, match="schema_version must be an integer") as exc_info:
        load_expectations(fixtures_dir, manifest_path)

    assert "Unsupported expectation manifest schema_version: 1" not in str(exc_info.value)


@pytest.mark.parametrize("schema_version", [-1, 0, 2, 999])
def test_expectation_manifest_rejects_unsupported_integer_schema_versions(
    tmp_path: Path,
    schema_version: int,
) -> None:
    fixtures_dir = one_fixture_dir(tmp_path)
    manifest_path = write_manifest(
        tmp_path / "expectations.json",
        f"""
        {{
          "schema_version": {schema_version},
          "fixtures": {{}}
        }}
        """,
    )

    with pytest.raises(ExpectationError, match=f"Unsupported expectation manifest schema_version: {schema_version}"):
        load_expectations(fixtures_dir, manifest_path)


def test_normal_expectation_manifest_still_loads() -> None:
    expectations = load_expectations(FIXTURES_DIR, EXPECTATION_PATH)

    assert expectations["valid/valid_single_span.json"] == {"verdict": "PASS", "rule_ids": []}
