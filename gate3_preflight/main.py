from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
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
from gate3_preflight.trace_api_adapter import (
    PreflightRetrievalError,
    PreflightTraceAPIAdapter,
    client_from_preflight_config,
    poll_and_retrieve,
)
from gate3_preflight.verification import verify_retrieved_trace


def main(argv: list[str] | None = None) -> int:
    del argv
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    runtime = Path(".traceguard") / "runtime" / "gate3_preflight" / batch_id
    summary: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    try:
        config = PreflightConfig.from_env()
        summary["config"] = config.non_secret_snapshot()
    except PreflightConfigError as exc:
        return _finish(runtime, summary, 2, "configuration", exc)

    try:
        client = client_from_preflight_config(config)
        adapter = PreflightTraceAPIAdapter(client)
        environment_check = adapter.run_environment_check()
        summary["environment_check"] = environment_check.to_dict()
        _write_json(runtime / "environment_check.json", environment_check.to_dict())
    except Exception as exc:
        return _finish(runtime, summary, 3, "environment_check", exc)

    exit_code = 0
    emission_manifest: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    for scenario in scenarios():
        scenario_summary: dict[str, object] = {}
        try:
            validate_scenario_expectations(scenario)
            emission = emit_scenario(scenario, config)
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
            retrieved = poll_and_retrieve(
                client,
                preflight_id=scenario.preflight_id,
                emitted_trace_id=emission.trace_id,
                timeout_seconds=config.poll_timeout_seconds,
                interval_seconds=config.poll_interval_seconds,
            )
            envelope = gate2_trace_to_gate3_envelope(retrieved.trace)
            trace = load_trace_payload(envelope)
            verification = verify_retrieved_trace(scenario=scenario, emission=emission, trace=retrieved.trace)
            evaluation = evaluate_trace(trace)
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

            _write_json(runtime / "retrieved" / f"{scenario.name}.normalized.json", envelope)
            _write_json(runtime / "verification" / f"{scenario.name}.json", verification.to_dict())
            _write_json(runtime / "evaluations" / f"{scenario.name}.json", evaluation.to_dict())
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
        except (PreflightExportError, PreflightRetrievalError) as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": str(exc)})
            exit_code = 3
            summary["scenarios"][scenario.name] = scenario_summary
            break
        except Exception as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": str(exc)})
            exit_code = 4
        summary["scenarios"][scenario.name] = scenario_summary

    summary["live_exit_code"] = exit_code
    _write_json(runtime / "emission_manifest.json", emission_manifest)
    _write_json(runtime / "preflight_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return exit_code


def _finish(runtime: Path, summary: dict[str, object], code: int, stage: str, exc: Exception) -> int:
    summary.update({"failed_stage": stage, "error_type": exc.__class__.__name__, "message": str(exc), "live_exit_code": code})
    _write_json(runtime / "preflight_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return code


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
