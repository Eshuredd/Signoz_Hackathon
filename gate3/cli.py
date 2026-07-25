from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gate3.evaluator import EvaluatorInternalError, evaluate_run_bundle, evaluate_trace
    from gate3.models import SUPPORTED_EXPECTATION_SCHEMA_VERSION, RuleStatus, Verdict, is_valid_integer
    from gate3.rules import RULES, RULE_BY_ID
    from gate3.trace_loader import RunBundleInputError, TraceInputError, load_run_bundle_file, load_trace_file
else:
    from .evaluator import EvaluatorInternalError, evaluate_run_bundle, evaluate_trace
    from .models import SUPPORTED_EXPECTATION_SCHEMA_VERSION, RuleStatus, Verdict, is_valid_integer
    from .rules import RULES, RULE_BY_ID
    from .trace_loader import RunBundleInputError, TraceInputError, load_run_bundle_file, load_trace_file


TRACE_EXPECTATION_PATH = Path(__file__).resolve().parent / "expected" / "trace_fixture_expectations.json"
RUN_EXPECTATION_PATH = Path(__file__).resolve().parent / "expected" / "run_fixture_expectations.json"
EXPECTATION_PATH = TRACE_EXPECTATION_PATH
TRACE_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "trace"
RUN_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "run"


class ExpectationError(Exception):
    """Raised when fixture expectations are invalid."""


class DuplicateJSONKeyError(ValueError):
    """Raised while decoding JSON that repeats an object key."""


def reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise DuplicateJSONKeyError(f"Duplicate JSON object key in expectation manifest: {key}")
        seen.add(key)
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TraceGuard Gate 3 deterministic telemetry evaluator")
    parser.add_argument("--debug", action="store_true", help="show stack traces for programming defects")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate_trace_parser = subparsers.add_parser("evaluate-trace")
    evaluate_trace_parser.add_argument("fixture")

    evaluate_run_parser = subparsers.add_parser("evaluate-run")
    evaluate_run_parser.add_argument("fixture")

    legacy_evaluate = subparsers.add_parser("evaluate", help="compatibility alias for evaluate-trace")
    legacy_evaluate.add_argument("fixture")

    subparsers.add_parser("evaluate-all")
    subparsers.add_parser("validate-fixtures")
    subparsers.add_parser("list-rules")

    args = parser.parse_args(argv)
    try:
        if args.command in {"evaluate-trace", "evaluate"}:
            return _evaluate_trace(Path(args.fixture), debug=args.debug)
        if args.command == "evaluate-run":
            return _evaluate_run(Path(args.fixture), debug=args.debug)
        if args.command == "evaluate-all":
            return _evaluate_all()
        if args.command == "validate-fixtures":
            return _validate_fixtures()
        if args.command == "list-rules":
            _print_json({"ruleset_version": "traceguard-telemetry-v2", "rules": [rule.to_catalog_dict() for rule in sorted(RULES, key=lambda item: (item.namespace, item.rule_id))]})
            return 0
    except (TraceInputError, RunBundleInputError, ExpectationError) as exc:
        _print_json({"error_type": exc.__class__.__name__, "message": str(exc)})
        return 2
    except EvaluatorInternalError as exc:
        if args.debug:
            raise
        _print_json({"error_type": "EvaluatorInternalError", "message": str(exc)})
        return 3
    return 2


def _evaluate_trace(path: Path, *, debug: bool) -> int:
    result = evaluate_trace(load_trace_file(path), debug=debug)
    _print_json(result.to_dict())
    return _verdict_exit_code(result.verdict)


def _evaluate_run(path: Path, *, debug: bool) -> int:
    result = evaluate_run_bundle(load_run_bundle_file(path), debug=debug)
    _print_json(result.to_dict())
    return _verdict_exit_code(result.verdict)


def _evaluate_all() -> int:
    trace_expectations = load_expectations(TRACE_FIXTURES_DIR, TRACE_EXPECTATION_PATH)
    run_expectations = load_expectations(RUN_FIXTURES_DIR, RUN_EXPECTATION_PATH)
    failures: list[dict[str, Any]] = []
    results: dict[str, Any] = {"trace": {}, "run": {}}
    for rel_path, expected in sorted(trace_expectations.items()):
        result = evaluate_trace(load_trace_file(TRACE_FIXTURES_DIR / rel_path))
        actual = actual_expectation(result.to_dict())
        results["trace"][rel_path] = actual
        if actual != expected:
            failures.append({"fixture": f"trace/{rel_path}", "expected": expected, "actual": actual})
    for rel_path, expected in sorted(run_expectations.items()):
        result = evaluate_run_bundle(load_run_bundle_file(RUN_FIXTURES_DIR / rel_path))
        actual = actual_expectation(result.to_dict())
        results["run"][rel_path] = actual
        if actual != expected:
            failures.append({"fixture": f"run/{rel_path}", "expected": expected, "actual": actual})
    _print_json({"fixture_count": len(trace_expectations) + len(run_expectations), "fixture_expectation_matches": not failures, "failed_fixture_expectations": failures, "results": results})
    return 0 if not failures else 1


def _validate_fixtures() -> int:
    trace_expectations = load_expectations(TRACE_FIXTURES_DIR, TRACE_EXPECTATION_PATH)
    run_expectations = load_expectations(RUN_FIXTURES_DIR, RUN_EXPECTATION_PATH)
    for rel_path in sorted(trace_expectations):
        load_trace_file(TRACE_FIXTURES_DIR / rel_path)
    for rel_path in sorted(run_expectations):
        load_run_bundle_file(RUN_FIXTURES_DIR / rel_path)
    _print_json({"trace_fixture_count": len(trace_expectations), "run_fixture_count": len(run_expectations), "fixtures_valid": True, "expectations_valid": True, "fixture_expectation_matches": None})
    return 0


def load_expectations(fixtures_dir: Path, expectations_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(expectations_path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_object_keys)
    except OSError as exc:
        raise ExpectationError(f"Unable to read expectation manifest: {expectations_path}") from exc
    except DuplicateJSONKeyError as exc:
        raise ExpectationError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ExpectationError(f"Invalid JSON in expectation manifest: {expectations_path}") from exc
    if not isinstance(payload, dict):
        raise ExpectationError("Expectation manifest must be an object with schema_version=1.")
    schema_version = payload.get("schema_version")
    if not is_valid_integer(schema_version):
        raise ExpectationError("Expectation manifest schema_version must be an integer.")
    if schema_version != SUPPORTED_EXPECTATION_SCHEMA_VERSION:
        raise ExpectationError(f"Unsupported expectation manifest schema_version: {schema_version!r}.")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, dict):
        raise ExpectationError("Expectation manifest must contain a fixtures object.")
    discovered = sorted(path.relative_to(fixtures_dir).as_posix() for path in fixtures_dir.rglob("*.json") if path.is_file())
    expected_paths = sorted(fixtures)
    missing_expectations = sorted(set(discovered) - set(expected_paths))
    missing_fixtures = sorted(set(expected_paths) - set(discovered))
    if missing_expectations:
        raise ExpectationError("Missing expectation for fixture(s): " + ", ".join(missing_expectations))
    if missing_fixtures:
        raise ExpectationError("Expectation references missing fixture(s): " + ", ".join(missing_fixtures))
    normalized: dict[str, dict[str, Any]] = {}
    registered = {rule.rule_id for rule in RULES}
    for rel_path, expectation in fixtures.items():
        if not isinstance(expectation, dict):
            raise ExpectationError(f"Expectation for {rel_path} must be an object.")
        verdict = expectation.get("verdict")
        rule_statuses = expectation.get("rule_statuses")
        if verdict not in {item.value for item in Verdict}:
            raise ExpectationError(f"Expectation for {rel_path} has invalid verdict.")
        if not isinstance(rule_statuses, dict):
            raise ExpectationError(f"Expectation for {rel_path} must contain rule_statuses object.")
        unknown = sorted(set(rule_statuses) - registered)
        missing = sorted(registered - set(rule_statuses))
        if unknown:
            raise ExpectationError(f"Expectation for {rel_path} references unknown rule ID(s): {', '.join(unknown)}")
        if missing:
            raise ExpectationError(f"Expectation for {rel_path} is missing rule status(es): {', '.join(missing)}")
        invalid = sorted(key for key, value in rule_statuses.items() if value not in {item.value for item in RuleStatus})
        if invalid:
            raise ExpectationError(f"Expectation for {rel_path} has invalid status for rule ID(s): {', '.join(invalid)}")
        normalized[rel_path] = {"verdict": verdict, "rule_statuses": {key: rule_statuses[key] for key in sorted(rule_statuses)}}
    return normalized


def actual_expectation(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "verdict": result["verdict"],
        "rule_statuses": {item["rule_id"]: item["status"] for item in result["rule_results"]},
    }


def _verdict_exit_code(verdict: Verdict) -> int:
    return {Verdict.PASS: 0, Verdict.PASS_WITH_WARNINGS: 10, Verdict.BLOCK: 20}[verdict]


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
