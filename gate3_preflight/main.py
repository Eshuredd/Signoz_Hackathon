from __future__ import annotations

import json
import sys
import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate3.evaluator import evaluate_trace
from gate3.models import EvaluationLevel, RULESET_VERSION
from gate3.rules import RULE_BY_ID
from gate3.trace_loader import load_trace_payload

from gate3_preflight.bridge import gate2_trace_to_gate3_envelope
from gate3_preflight.config import PreflightConfig, PreflightConfigError
from gate3_preflight.exporter import PreflightExportError, emit_scenario
from gate3_preflight.scenarios import scenarios, validate_scenario_expectations
from gate3_preflight.scenarios import scenario_catalogue
from gate3_preflight.trace_api_adapter import (
    AuthenticationFailure,
    AuthorizationFailure,
    ConfigurationError,
    ConnectionFailure,
    InvalidResponseSchema,
    PreflightRetrievalError,
    PreflightTraceAPIAdapter,
    RequestTimeout,
    UnsupportedAPIOperation,
    client_from_preflight_config,
    poll_and_retrieve,
)
from gate3_preflight.verification import verify_retrieved_trace


KNOWN_INFRASTRUCTURE_EXCEPTIONS = (
    AuthenticationFailure,
    AuthorizationFailure,
    ConfigurationError,
    ConnectionFailure,
    RequestTimeout,
    UnsupportedAPIOperation,
    InvalidResponseSchema,
    PreflightExportError,
    PreflightRetrievalError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TraceGuard Gate 3 live preflight checks.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-environment", action="store_true", help="validate config and SigNoz Trace API access without exporting telemetry")
    mode.add_argument("--list-scenarios", action="store_true", help="print the static sanitized scenario catalogue as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print(json.dumps(scenario_catalogue(), indent=2, sort_keys=True))
        return 0
    return run_preflight(check_environment_only=args.check_environment)


def run_preflight(
    *,
    check_environment_only: bool = False,
    config_factory: Callable[[], PreflightConfig] = PreflightConfig.from_env,
    client_factory: Callable[[PreflightConfig], object] = client_from_preflight_config,
    scenario_factory: Callable[[], object] = scenarios,
    emit: Callable[..., object] = emit_scenario,
    poll: Callable[..., object] = poll_and_retrieve,
    verify: Callable[..., object] = verify_retrieved_trace,
    evaluate: Callable[..., object] = evaluate_trace,
    write_json: Callable[[Path, dict[str, object]], None] | None = None,
) -> int:
    write_json = write_json or _write_json
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    runtime = Path(".traceguard") / "runtime" / "gate3_preflight" / batch_id
    summary: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    try:
        config = config_factory()
        summary["config"] = config.non_secret_snapshot()
    except PreflightConfigError as exc:
        return _finish(runtime, summary, 2, "configuration", exc, write_json)

    try:
        client = client_factory(config)
        adapter = PreflightTraceAPIAdapter(client)
        environment_check = adapter.run_environment_check()
        summary["environment_check"] = environment_check.to_dict()
        write_json(runtime / "environment_check.json", environment_check.to_dict())
    except KNOWN_INFRASTRUCTURE_EXCEPTIONS as exc:
        return _finish(runtime, summary, 3, "environment_check", exc, write_json)
    except Exception as exc:
        return _finish(runtime, summary, 4, "environment_check", exc, write_json)

    if check_environment_only:
        summary["live_exit_code"] = 0
        try:
            write_json(runtime / "preflight_summary.json", summary)
        except Exception as exc:
            return _finish(runtime, summary, 4, "artifact_writing", exc, write_json)
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 0

    exit_code = 0
    emission_manifest: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    try:
        scenario_items = scenario_factory()
    except (PreflightConfigError, ValueError) as exc:
        return _finish(runtime, summary, 2, "scenario_catalogue", exc, write_json)
    except Exception as exc:
        return _finish(runtime, summary, 4, "scenario_catalogue", exc, write_json)

    for scenario in scenario_items:
        scenario_summary: dict[str, object] = {}
        try:
            validate_scenario_expectations(scenario)
            emission = emit(scenario, config)
            scenario_summary["emitted_trace_id"] = emission.trace_id
            scenario_summary["emitted_span_ids"] = emission.span_ids_by_name
            scenario_summary["emitted_parent_span_ids"] = emission.parent_span_ids_by_name
            emission_manifest["scenarios"][scenario.name] = {
                "trace_id": emission.trace_id,
                "root_span_id": emission.root_span_id,
                "span_ids_by_name": emission.span_ids_by_name,
                "parent_span_ids_by_name": emission.parent_span_ids_by_name,
                "completed_span_count": emission.completed_span_count,
                "exported": emission.exported,
                "exported_at": emission.exported_at,
            }
            retrieved = poll(
                client,
                preflight_id=scenario.preflight_id,
                emitted_trace_id=emission.trace_id,
                timeout_seconds=config.poll_timeout_seconds,
                interval_seconds=config.poll_interval_seconds,
            )
            envelope = gate2_trace_to_gate3_envelope(retrieved.trace)
            trace = load_trace_payload(envelope)
            verification = verify(scenario=scenario, emission=emission, trace=retrieved.trace)
            evaluation = evaluate(trace)
            actual_statuses = {item.rule_id: item.status.value for item in evaluation.rule_results}
            duplicate_rule_ids = len(actual_statuses) != len(evaluation.rule_results)
            exact_status_match = actual_statuses == scenario.expected_statuses
            verdict_match = evaluation.verdict.value == scenario.expected_verdict
            evaluator_contract_match = (
                not duplicate_rule_ids
                and set(actual_statuses) == set(RULE_BY_ID)
                and evaluation.ruleset_version == RULESET_VERSION
                and evaluation.evaluation_level == EvaluationLevel.TRACE
            )
            matched = verification.passed and exact_status_match and verdict_match and evaluator_contract_match

            write_json(runtime / "retrieved" / f"{scenario.name}.normalized.json", envelope)
            write_json(runtime / "verification" / f"{scenario.name}.json", verification.to_dict())
            write_json(runtime / "evaluations" / f"{scenario.name}.json", evaluation.to_dict())
            scenario_summary.update(
                {
                    "discovered_trace_ids": list(retrieved.discovered_trace_ids),
                    "retrieved_trace_id": retrieved.trace.trace_id,
                    "search_attempts": retrieved.search_attempt_count,
                    "retrieval_attempts": retrieved.retrieval_attempt_count,
                    "elapsed_ms": retrieved.elapsed_ms,
                    "verification": verification.to_dict(),
                    "expected_verdict": scenario.expected_verdict,
                    "actual_verdict": evaluation.verdict.value,
                    "expected_rule_statuses": scenario.expected_statuses,
                    "actual_rule_statuses": actual_statuses,
                    "exact_status_match": exact_status_match,
                    "verdict_match": verdict_match,
                    "evaluator_contract_match": evaluator_contract_match,
                    "matched_expectations": matched,
                }
            )
            if not matched:
                exit_code = 1
        except KNOWN_INFRASTRUCTURE_EXCEPTIONS as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": _sanitize_message(exc), "failed_stage": "scenario"})
            exit_code = 3
            summary["scenarios"][scenario.name] = scenario_summary
            break
        except Exception as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": _sanitize_message(exc), "failed_stage": "scenario"})
            exit_code = 4
        summary["scenarios"][scenario.name] = scenario_summary

    summary["live_exit_code"] = exit_code
    try:
        write_json(runtime / "emission_manifest.json", emission_manifest)
        write_json(runtime / "preflight_summary.json", summary)
    except Exception as exc:
        return _finish(runtime, summary, 4, "artifact_writing", exc, write_json)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return exit_code


def _finish(runtime: Path, summary: dict[str, object], code: int, stage: str, exc: Exception, write_json: Callable[[Path, dict[str, object]], None] = None) -> int:
    writer = write_json or _write_json
    summary.update({"failed_stage": stage, "error_type": exc.__class__.__name__, "message": _sanitize_message(exc), "live_exit_code": code})
    if stage == "artifact_writing":
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return code
    try:
        writer(runtime / "preflight_summary.json", summary)
    except Exception:
        summary.update({"failed_stage": "artifact_writing", "error_type": "ArtifactWriteError", "message": "failed to write required runtime artifact", "live_exit_code": 4})
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 4
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return code


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _sanitize_message(exc: Exception) -> str:
    text = str(exc)
    for marker in ("SIGNOZ-API-KEY", "Authorization", "Bearer", "Cookie", "password", "token"):
        if marker.lower() in text.lower():
            return "sanitized error message withheld"
    return text


if __name__ == "__main__":
    raise SystemExit(main())
