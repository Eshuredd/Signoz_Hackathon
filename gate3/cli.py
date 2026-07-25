from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gate3.evaluator import EvaluatorInternalError, evaluate_trace
    from gate3.models import Verdict
    from gate3.rules import RULE_BY_ID
    from gate3.trace_loader import TraceInputError, load_trace_file
else:
    from .evaluator import EvaluatorInternalError, evaluate_trace
    from .models import Verdict
    from .rules import RULE_BY_ID
    from .trace_loader import TraceInputError, load_trace_file


EXPECTATION_PATH = Path(__file__).resolve().parent / "expected" / "fixture_expectations.json"


class ExpectationError(Exception):
    """Raised when fixture expectations are invalid."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate 3A deterministic telemetry evaluator")
    parser.add_argument("--debug", action="store_true", help="show stack traces for programming defects")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("fixture")
    evaluate.add_argument("--format", choices=("json", "text"), default="json")

    evaluate_all = subparsers.add_parser("evaluate-all")
    evaluate_all.add_argument("fixtures_dir")
    evaluate_all.add_argument("--expectations", default=str(EXPECTATION_PATH))

    validate = subparsers.add_parser("validate-fixtures")
    validate.add_argument("fixtures_dir")
    validate.add_argument("--expectations", default=str(EXPECTATION_PATH))

    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            return _evaluate(Path(args.fixture), args.format)
        if args.command == "evaluate-all":
            return _evaluate_all(Path(args.fixtures_dir), Path(args.expectations))
        if args.command == "validate-fixtures":
            return _validate_fixtures(Path(args.fixtures_dir), Path(args.expectations))
    except (TraceInputError, ExpectationError) as exc:
        _print_json({"error_type": exc.__class__.__name__, "message": str(exc)})
        return 2
    except EvaluatorInternalError as exc:
        if args.debug:
            raise
        _print_json({"error_type": "EvaluatorInternalError", "message": str(exc)})
        return 3
    return 2


def _evaluate(path: Path, output_format: str) -> int:
    result = evaluate_trace(load_trace_file(path))
    if output_format == "text":
        print(_text_result(result.to_dict()))
    else:
        _print_json(result.to_dict())
    return {Verdict.PASS: 0, Verdict.WARN: 10, Verdict.BLOCK: 20}[result.verdict]


def _evaluate_all(fixtures_dir: Path, expectations_path: Path) -> int:
    expectations = load_expectations(fixtures_dir, expectations_path)
    failures: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for rel_path, expected in sorted(expectations.items()):
        result = evaluate_trace(load_trace_file(fixtures_dir / rel_path))
        actual_rule_ids = sorted({finding.rule_id for finding in result.findings})
        actual = {"verdict": result.verdict.value, "rule_ids": actual_rule_ids}
        results[rel_path] = actual
        if actual != expected:
            failures.append({"fixture": rel_path, "expected": expected, "actual": actual})
    _print_json(
        {
            "fixture_count": len(expectations),
            "fixture_expectation_matches": len(failures) == 0,
            "failed_fixture_expectations": failures,
            "results": results,
        }
    )
    return 0 if not failures else 1


def _validate_fixtures(fixtures_dir: Path, expectations_path: Path) -> int:
    expectations = load_expectations(fixtures_dir, expectations_path)
    for rel_path in sorted(expectations):
        load_trace_file(fixtures_dir / rel_path)
    _print_json(
        {
            "fixture_count": len(expectations),
            "fixtures_valid": True,
            "expectations_valid": True,
            "fixture_expectation_matches": None,
        }
    )
    return 0


def load_expectations(fixtures_dir: Path, expectations_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(expectations_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExpectationError(f"Unable to read expectation manifest: {expectations_path}") from exc
    except json.JSONDecodeError as exc:
        raise ExpectationError(f"Invalid JSON in expectation manifest: {expectations_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ExpectationError("Expectation manifest must be an object with schema_version=1.")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, dict):
        raise ExpectationError("Expectation manifest must contain a fixtures object.")

    discovered = sorted(
        path.relative_to(fixtures_dir).as_posix()
        for path in fixtures_dir.rglob("*.json")
        if path.is_file()
    )
    expected_paths = sorted(fixtures)
    missing_expectations = sorted(set(discovered) - set(expected_paths))
    missing_fixtures = sorted(set(expected_paths) - set(discovered))
    if missing_expectations:
        raise ExpectationError("Missing expectation for fixture(s): " + ", ".join(missing_expectations))
    if missing_fixtures:
        raise ExpectationError("Expectation references missing fixture(s): " + ", ".join(missing_fixtures))

    normalized: dict[str, dict[str, Any]] = {}
    for rel_path, expectation in fixtures.items():
        if not isinstance(expectation, dict):
            raise ExpectationError(f"Expectation for {rel_path} must be an object.")
        verdict = expectation.get("verdict")
        rule_ids = expectation.get("rule_ids")
        if verdict not in {item.value for item in Verdict}:
            raise ExpectationError(f"Expectation for {rel_path} has invalid verdict.")
        if not isinstance(rule_ids, list) or any(not isinstance(item, str) for item in rule_ids):
            raise ExpectationError(f"Expectation for {rel_path} must contain a rule_ids string list.")
        if rule_ids != sorted(rule_ids):
            raise ExpectationError(f"Expectation rule_ids for {rel_path} must be sorted.")
        unknown = sorted(set(rule_ids) - set(RULE_BY_ID))
        if unknown:
            raise ExpectationError(f"Expectation for {rel_path} references unknown rule ID(s): {', '.join(unknown)}")
        normalized[rel_path] = {"verdict": verdict, "rule_ids": rule_ids}
    return normalized


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def _text_result(result: dict[str, Any]) -> str:
    lines = [f"{result['verdict']} {result['trace_id']}"]
    for finding in result["findings"]:
        span_ids = ",".join(finding["span_ids"]) if finding["span_ids"] else "-"
        lines.append(f"{finding['rule_id']} {finding['severity']} {span_ids} {finding['message']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
