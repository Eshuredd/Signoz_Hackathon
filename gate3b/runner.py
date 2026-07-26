from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from gate3.evaluator import evaluate_run_bundle
from gate3.models import EvaluationLevel
from gate3.rules import RULE_BY_ID
from gate3.trace_loader import load_run_bundle_payload

from .bridge import build_gate3_run_bundle
from .config import Gate3BConfig
from .log_api_adapter import Gate3BLogRetrievalError, log_api_contract, poll_and_retrieve_logs, verify_log_api_access
from .log_exporter import Gate3BLogExportError, emit_logs
from .models import AUTHORITATIVE_LOG_SOURCE, AUTHORITATIVE_TRACE_SOURCE, EnvironmentCheckResult, Gate3BConfigError, Gate3BInfrastructureError, RuntimeScenario, now_iso
from .scenarios import SCENARIO_DEFINITIONS, get_definition, runtime_scenario, scenario_catalogue, validate_scenario_catalogue
from .trace_api_adapter import NON_RETRY, client_from_config, poll_and_retrieve_traces, verify_trace_api_access
from .trace_exporter import Gate3BTraceExportError, emit_traces
from .verification import verify_preservation


KNOWN_INFRA = (*NON_RETRY, Gate3BInfrastructureError, Gate3BTraceExportError, Gate3BLogExportError, Gate3BLogRetrievalError)


@dataclass(frozen=True)
class RunnerDependencies:
    config_factory: Callable[[], Gate3BConfig] = Gate3BConfig.from_env
    client_factory: Callable[[Gate3BConfig], object] = client_from_config
    trace_emit: Callable[..., object] = emit_traces
    log_emit: Callable[..., object] = emit_logs
    trace_poll: Callable[..., object] = poll_and_retrieve_traces
    log_poll: Callable[..., object] = poll_and_retrieve_logs
    evaluator: Callable[..., object] = evaluate_run_bundle
    write_json: Callable[[Path, dict[str, object]], None] | None = None


def run_gate3b(
    *,
    selected_scenario_name: str | None = None,
    check_environment_only: bool = False,
    deps: RunnerDependencies = RunnerDependencies(),
) -> int:
    writer = deps.write_json or write_json
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    runtime = Path(".traceguard") / "runtime" / "gate3b" / batch_id
    summary: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}, "authoritative_sources": {"trace": AUTHORITATIVE_TRACE_SOURCE, "log": AUTHORITATIVE_LOG_SOURCE}}
    try:
        validate_scenario_catalogue(SCENARIO_DEFINITIONS)
        if selected_scenario_name:
            selected = (get_definition(selected_scenario_name),)
        else:
            selected = SCENARIO_DEFINITIONS
        config = deps.config_factory()
        summary["config"] = config.non_secret_snapshot()
    except (Gate3BConfigError, ValueError, KeyError) as exc:
        return _finish(runtime, summary, 2, "configuration_or_catalogue", exc, writer)
    except Exception as exc:
        return _finish(runtime, summary, 4, "configuration_or_catalogue", exc, writer)

    try:
        client = deps.client_factory(config)
        env = run_environment_check(config, client)
        summary["environment_check"] = env.to_dict()
        writer(runtime / "environment_check.json", env.to_dict())
        writer(runtime / "scenario_catalog.json", scenario_catalogue())
        writer(runtime / "log_api_contract.json", log_api_contract(env.signoz_version))
    except KNOWN_INFRA as exc:
        return _finish(runtime, summary, 3, "environment_check", exc, writer)
    except Exception as exc:
        return _finish(runtime, summary, 4, "environment_check", exc, writer)

    if check_environment_only:
        summary["live_exit_code"] = 0
        try:
            writer(runtime / "gate3b_summary.json", summary)
        except Exception as exc:
            return _finish(runtime, summary, 4, "artifact_writing", exc, writer)
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 0

    exit_code = 0
    trace_manifest: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    log_manifest: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    for definition in selected:
        scenario = runtime_scenario(definition, batch_id)
        scenario_summary: dict[str, object] = {"scenario_id": scenario.scenario_id, "agent_run_id": scenario.agent_run_id}
        try:
            trace_emission = deps.trace_emit(scenario, config)
            log_emission = deps.log_emit(scenario, config, trace_emission)
            trace_manifest["scenarios"][scenario.name] = trace_emission.to_dict()
            log_manifest["scenarios"][scenario.name] = log_emission.to_dict()

            trace_retrieval = deps.trace_poll(client, scenario, trace_emission.emitted_trace_ids, timeout_seconds=config.ingestion_timeout_seconds, interval_seconds=config.poll_interval_seconds)
            log_retrieval = deps.log_poll(client, scenario, log_emission.log_ids, timeout_seconds=config.ingestion_timeout_seconds, interval_seconds=config.poll_interval_seconds)

            verification = verify_preservation(scenario, trace_emission, trace_retrieval.traces, log_emission, log_retrieval.logs)
            bundle_payload = build_gate3_run_bundle(
                scenario.agent_run_id,
                trace_retrieval.traces,
                log_retrieval.logs,
                {"gate": "3B", "scenario_name": scenario.name, "scenario_id": scenario.scenario_id, "batch_id": batch_id},
            )
            bundle = load_run_bundle_payload(bundle_payload)
            evaluation = deps.evaluator(bundle)
            actual_statuses = {item.rule_id: item.status.value for item in evaluation.rule_results}
            exact_status_match = actual_statuses == definition.expected_rule_statuses
            verdict_match = evaluation.verdict.value == definition.expected_verdict
            evaluator_contract_match = (
                len(actual_statuses) == 14
                and len(evaluation.rule_results) == 14
                and set(actual_statuses) == set(RULE_BY_ID)
                and evaluation.ruleset_version == RULESET_VERSION
                and evaluation.evaluation_level == EvaluationLevel.RUN
            )
            matched = verification.passed and exact_status_match and verdict_match and evaluator_contract_match
            for trace in trace_retrieval.traces:
                writer(runtime / "retrieved_traces" / scenario.name / f"{trace.trace_id}.normalized.json", trace.to_dict())
            writer(runtime / "retrieved_logs" / f"{scenario.name}.normalized.json", {"logs": [log.to_dict() for log in log_retrieval.logs], "sanitized": True})
            writer(runtime / "verification" / f"{scenario.name}.json", verification.to_dict())
            writer(runtime / "run_bundles" / f"{scenario.name}.json", bundle_payload)
            writer(runtime / "evaluations" / f"{scenario.name}.json", evaluation.to_dict())
            scenario_summary.update(
                {
                    "emitted_trace_ids": list(trace_emission.emitted_trace_ids),
                    "discovered_trace_ids": list(trace_retrieval.discovered_trace_ids),
                    "retrieved_trace_ids": [trace.trace_id for trace in trace_retrieval.traces],
                    "emitted_span_ids": trace_emission.span_ids_by_trace_id_and_name,
                    "retrieved_span_ids": {trace.trace_id: {span.span_name: span.span_id for span in trace.spans} for trace in trace_retrieval.traces},
                    "emitted_log_ids": list(log_emission.log_ids),
                    "retrieved_log_ids": [log.log_id for log in log_retrieval.logs],
                    "trace_preservation_result": verification.trace_preservation_result,
                    "log_preservation_result": verification.log_preservation_result,
                    "expected_trace_count": definition.expected_trace_count,
                    "actual_trace_count": len(trace_retrieval.traces),
                    "expected_log_count": definition.expected_log_count,
                    "actual_log_count": len(log_retrieval.logs),
                    "expected_verdict": definition.expected_verdict,
                    "actual_verdict": evaluation.verdict.value,
                    "expected_rule_statuses": definition.expected_rule_statuses,
                    "actual_rule_statuses": actual_statuses,
                    "exact_status_match": exact_status_match,
                    "verdict_match": verdict_match,
                    "evaluator_contract_match": evaluator_contract_match,
                    "matched_expectations": matched,
                    "search_attempts": trace_retrieval.stats.search_attempt_count,
                    "retrieval_attempts": trace_retrieval.stats.retrieval_attempt_count,
                    "elapsed_ms": trace_retrieval.stats.elapsed_ms + log_retrieval.stats.elapsed_ms,
                }
            )
            if not matched:
                exit_code = max(exit_code, 1)
        except KNOWN_INFRA as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": sanitize_message(exc), "failed_stage": "scenario"})
            summary["scenarios"][scenario.name] = scenario_summary
            exit_code = 3
            break
        except Exception as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": sanitize_message(exc), "failed_stage": "scenario"})
            summary["scenarios"][scenario.name] = scenario_summary
            exit_code = 4
            break
        summary["scenarios"][scenario.name] = scenario_summary

    completed = [item for item in summary["scenarios"].values() if isinstance(item, dict) and "actual_verdict" in item]
    matched = [item for item in completed if item.get("matched_expectations") is True]
    summary.update(
        {
            "captured_at": now_iso(),
            "scenario_count": len(selected),
            "completed_count": len(completed),
            "matched_count": len(matched),
            "failed_count": len(completed) - len(matched),
            "all_expectations_matched": len(completed) == len(selected) and len(matched) == len(selected),
            "live_exit_code": exit_code,
        }
    )
    try:
        writer(runtime / "trace_emission_manifest.json", trace_manifest)
        writer(runtime / "log_emission_manifest.json", log_manifest)
        writer(runtime / "gate3b_summary.json", summary)
    except Exception as exc:
        return _finish(runtime, summary, 4, "artifact_writing", exc, writer)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return exit_code


def run_environment_check(config: Gate3BConfig, client: object) -> EnvironmentCheckResult:
    health = client.health_check()
    if health.get("status") != "ok":
        raise Gate3BInfrastructureError("SigNoz health response did not report status=ok.")
    version_payload = client.version()
    signoz_version = str(version_payload.get("version") or version_payload.get("tag") or version_payload.get("buildVersion") or "unknown")
    return EnvironmentCheckResult(
        health_ok=True,
        signoz_version=signoz_version,
        authenticated_trace_api_access=verify_trace_api_access(client),
        authenticated_log_api_access=verify_log_api_access(client),
        trace_otlp_endpoint=config.trace_otlp_endpoint,
        log_otlp_endpoint=config.log_otlp_endpoint,
        checked_at=now_iso(),
    )


def _finish(runtime: Path, summary: dict[str, object], code: int, stage: str, exc: Exception, writer: Callable[[Path, dict[str, object]], None]) -> int:
    summary.update({"failed_stage": stage, "error_type": exc.__class__.__name__, "message": sanitize_message(exc), "live_exit_code": code})
    if stage != "artifact_writing":
        try:
            writer(runtime / "gate3b_summary.json", summary)
        except Exception:
            summary.update({"failed_stage": "artifact_writing", "error_type": "ArtifactWriteError", "message": "failed to write required runtime artifact", "live_exit_code": 4})
            code = 4
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return code


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def sanitize_message(exc: Exception) -> str:
    text = str(exc)
    for marker in ("SIGNOZ-API-KEY", "Authorization", "Bearer", "Cookie", "password", "token"):
        if marker.lower() in text.lower():
            return "sanitized error message withheld"
    return text
