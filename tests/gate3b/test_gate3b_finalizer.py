from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from gate2.models import Source, Span, Trace
from gate3.evaluator import evaluate_run_bundle
from gate3.trace_loader import load_run_bundle_payload
from gate3b import finalize_evidence
from gate3b.bridge import build_gate3_run_bundle
from gate3b.finalize_evidence import EXPECTED_VERIFICATION_COMMANDS, PYTEST_VERIFICATION_NAMES, REQUIRED_VERIFICATION_NAMES, GitProvenance, VerificationCommandResult
from gate3b.models import LOG_ID_ATTR, TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, LogEmissionResult, RetrievedLog, RuntimeScenario, TraceEmissionResult
from gate3b.scenarios import SCENARIO_DEFINITIONS
from gate3b.verification import verify_preservation


VALID_BATCH_ID = "20260101T000000Z-abcdef123456"
SOURCE_SHA = "1" * 40


def clean_provenance(_summary: dict[str, object]) -> GitProvenance:
    return GitProvenance(SOURCE_SHA, True, True)


def complete_summary() -> dict[str, object]:
    scenarios = {}
    for definition in SCENARIO_DEFINITIONS:
        trace_ids = ["a" * 32, "b" * 32] if definition.name == "block_fragmented_run" else ["a" * 32]
        log_ids = [f"log-{index}" for index in range(definition.expected_log_count)]
        trace_details = {
            "trace_ids_match": True,
            "span_count_match": True,
            "span_names_match": True,
            "span_ids_match": True,
            "parent_relationships_match": True,
            "canonical_attributes_match": True,
            "run_id_preserved": True,
            "fragmentation_preserved": True,
            "scenario_correlation_match": True,
            "service_identity_preserved": True,
            "timing_preserved": True,
            "errors": [],
            "passed": True,
        }
        log_details = {
            "log_ids_match": True,
            "scenario_correlation_match": True,
            "trace_span_correlation_match": True,
            "agent_run_id_preserved": True,
            "intentional_mismatch_preserved": True,
            "body_preserved": True,
            "timestamp_preserved": True,
            "service_identity_preserved": True,
            "resource_attributes_preserved": True,
            "errors": [],
            "passed": True,
        }
        scenarios[definition.name] = {
            "emitted_trace_ids": trace_ids,
            "discovered_trace_ids": trace_ids,
            "retrieved_trace_ids": trace_ids,
            "emitted_log_ids": log_ids,
            "retrieved_log_ids": log_ids,
            "expected_trace_count": definition.expected_trace_count,
            "actual_trace_count": definition.expected_trace_count,
            "expected_log_count": definition.expected_log_count,
            "actual_log_count": definition.expected_log_count,
            "expected_verdict": definition.expected_verdict,
            "actual_verdict": definition.expected_verdict,
            "expected_rule_statuses": definition.expected_rule_statuses,
            "actual_rule_statuses": definition.expected_rule_statuses,
            "matched_expectations": True,
            "trace_preservation_result": True,
            "log_preservation_result": True,
            "trace_preservation_details": trace_details,
            "log_preservation_details": log_details,
            "preservation_errors": [],
            "preservation_passed": True,
            "exact_status_match": True,
            "verdict_match": True,
            "evaluator_contract_match": True,
        }
    return {
        "batch_id": VALID_BATCH_ID,
        "captured_at": "2026-01-01T00:00:00Z",
        "source_commit_sha": SOURCE_SHA,
        "source_worktree_clean": True,
        "config": {
            "SIGNOZ_BASE_URL": "http://localhost:8080",
            "SIGNOZ_API_KEY": "<set>",
            "TRACEGUARD_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
            "TRACEGUARD_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
            "SIGNOZ_REQUEST_TIMEOUT_SECONDS": 10.0,
            "TRACEGUARD_GATE3B_INGEST_TIMEOUT_SECONDS": 60.0,
            "TRACEGUARD_GATE3B_POLL_INTERVAL_SECONDS": 2.0,
            "TRACEGUARD_OTLP_TIMEOUT_SECONDS": 10.0,
            "TRACEGUARD_GATE3B_SERVICE_NAME": "svc",
        },
        "scenario_count": 4,
        "completed_count": 4,
        "matched_count": 4,
        "failed_count": 0,
        "all_expectations_matched": True,
        "live_exit_code": 0,
        "environment_check": {
            "health_ok": True,
            "authenticated_trace_api_access": True,
            "authenticated_log_api_access": True,
            "signoz_version": "test",
            "trace_otlp_endpoint": "http://localhost:4318/v1/traces",
            "log_otlp_endpoint": "http://localhost:4318/v1/logs",
            "checked_at": "2026-01-01T00:00:00Z",
        },
        "scenarios": scenarios,
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_complete_runtime(root: Path, batch_id: str = VALID_BATCH_ID) -> dict[str, object]:
    runtime = root / batch_id
    if runtime.exists():
        shutil.rmtree(runtime)
    env = {
        "health_ok": True,
        "authenticated_trace_api_access": True,
        "authenticated_log_api_access": True,
        "signoz_version": "test",
        "trace_otlp_endpoint": "http://localhost:4318/v1/traces",
        "log_otlp_endpoint": "http://localhost:4318/v1/logs",
        "checked_at": "2026-01-01T00:00:00Z",
    }
    summary: dict[str, object] = {
        "batch_id": batch_id,
        "captured_at": "2026-01-01T00:00:00Z",
        "source_commit_sha": SOURCE_SHA,
        "source_worktree_clean": True,
        "config": {
            "SIGNOZ_BASE_URL": "http://localhost:8080",
            "SIGNOZ_API_KEY": "<set>",
            "TRACEGUARD_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
            "TRACEGUARD_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
            "SIGNOZ_REQUEST_TIMEOUT_SECONDS": 10.0,
            "TRACEGUARD_GATE3B_INGEST_TIMEOUT_SECONDS": 60.0,
            "TRACEGUARD_GATE3B_POLL_INTERVAL_SECONDS": 2.0,
            "TRACEGUARD_OTLP_TIMEOUT_SECONDS": 10.0,
            "TRACEGUARD_GATE3B_SERVICE_NAME": "svc",
        },
        "scenario_count": 4,
        "completed_count": 4,
        "matched_count": 4,
        "failed_count": 0,
        "all_expectations_matched": True,
        "live_exit_code": 0,
        "environment_check": env,
        "scenarios": {},
    }
    trace_manifest: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    log_manifest: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "scenarios": {}}
    for scenario_index, definition in enumerate(SCENARIO_DEFINITIONS, 1):
        scenario_id = f"scenario-{scenario_index}"
        agent_run_id = f"run-{scenario_index}"
        log_ids = tuple(f"log-{scenario_index}-{index}" for index in range(definition.expected_log_count))
        scenario = RuntimeScenario(definition, batch_id, scenario_id, agent_run_id, log_ids)
        trace_ids = tuple(f"{scenario_index}{index + 1:031x}"[-32:] for index in range(definition.expected_trace_count))
        traces: list[Trace] = []
        span_maps: dict[str, dict[str, str]] = {}
        parent_maps: dict[str, dict[str, str | None]] = {}
        expected_attrs: dict[str, dict[str, dict[str, object]]] = {}
        root_ids: dict[str, str] = {}
        now = datetime(2026, 1, 1, 0, 0, scenario_index, tzinfo=UTC)
        for trace_index, trace_id in enumerate(trace_ids):
            root = f"{scenario_index}{trace_index}1".rjust(16, "1")[-16:]
            tool = f"{scenario_index}{trace_index}2".rjust(16, "2")[-16:]
            model = f"{scenario_index}{trace_index}3".rjust(16, "3")[-16:]
            base = {TRACE_BATCH_ATTR: batch_id, TRACE_SCENARIO_ATTR: scenario_id, TRACE_SCENARIO_NAME_ATTR: definition.name}
            attrs = {
                "agent.run": base | {"agent.run_id": agent_run_id, "agent.name": "traceguard-gate3b", "agent.status": "success"},
                "tool.call": base | {"tool.status": "success"},
                "model.call": base | {"gen_ai.request.model": "gpt-gate3b", "gen_ai.usage.input_tokens": 7 + trace_index, "gen_ai.usage.output_tokens": 11 + trace_index},
            }
            spans = [
                Span(trace_id, root, None, "agent.run", now, now, 1, {}, attrs["agent.run"], {"service.name": "svc"}, "svc"),
                Span(trace_id, tool, root, "tool.call", now, now, 1, {}, attrs["tool.call"], {"service.name": "svc"}, "svc"),
                Span(trace_id, model, root, "model.call", now, now, 1, {}, attrs["model.call"], {"service.name": "svc"}, "svc"),
            ]
            traces.append(Trace(trace_id, spans, now, Source.TRACE_API))
            root_ids[trace_id] = root
            span_maps[trace_id] = {"agent.run": root, "tool.call": tool, "model.call": model}
            parent_maps[trace_id] = {"agent.run": None, "tool.call": root, "model.call": root}
            expected_attrs[trace_id] = attrs
        trace_emission = TraceEmissionResult(definition.name, agent_run_id, "svc", trace_ids, root_ids, span_maps, parent_maps, expected_attrs, "2026-01-01T00:00:00Z")
        expected_agent_run_ids: dict[str, str] = {}
        expected_trace_ids: dict[str, str] = {}
        expected_span_ids: dict[str, str] = {}
        bodies: dict[str, str] = {}
        logs: list[RetrievedLog] = []
        for log_index, spec in enumerate(definition.log_plan):
            log_id = log_ids[log_index]
            trace_id = trace_ids[min(log_index, len(trace_ids) - 1)]
            span_id = span_maps[trace_id][spec.span_name]
            run_id = agent_run_id if spec.agent_run_id_mode == "match" else f"{agent_run_id}-mismatch"
            body = f"{spec.body}: {definition.name}:{spec.name}"
            attrs = {
                LOG_ID_ATTR: log_id,
                TRACE_BATCH_ATTR: batch_id,
                TRACE_SCENARIO_ATTR: scenario_id,
                TRACE_SCENARIO_NAME_ATTR: definition.name,
                "agent.run_id": run_id,
                "trace_id": trace_id,
                "span_id": span_id,
            }
            logs.append(RetrievedLog(log_id, now.isoformat().replace("+00:00", "Z"), trace_id, span_id, body, attrs, {"service.name": "svc"}, "svc"))
            expected_agent_run_ids[log_id] = run_id
            expected_trace_ids[log_id] = trace_id
            expected_span_ids[log_id] = span_id
            bodies[log_id] = body
        log_emission = LogEmissionResult(definition.name, "svc", log_ids, expected_agent_run_ids, expected_trace_ids, expected_span_ids, bodies, "2026-01-01T00:00:00Z")
        verification = verify_preservation(scenario, trace_emission, tuple(traces), log_emission, tuple(logs))
        bundle_payload = build_gate3_run_bundle(agent_run_id, tuple(traces), tuple(logs), {"gate": "3B", "scenario_name": definition.name, "scenario_id": scenario_id, "batch_id": batch_id})
        evaluation = evaluate_run_bundle(load_run_bundle_payload(bundle_payload))
        actual_statuses = {item.rule_id: item.status.value for item in evaluation.rule_results}
        trace_manifest["scenarios"][definition.name] = trace_emission.to_dict()  # type: ignore[index]
        log_manifest["scenarios"][definition.name] = log_emission.to_dict()  # type: ignore[index]
        for trace in traces:
            write_json(runtime / "retrieved_traces" / definition.name / f"{trace.trace_id}.normalized.json", trace.to_dict())
        write_json(runtime / "retrieved_logs" / f"{definition.name}.normalized.json", {"logs": [log.to_dict() for log in logs], "sanitized": True})
        write_json(runtime / "verification" / f"{definition.name}.json", verification.to_dict())
        write_json(runtime / "run_bundles" / f"{definition.name}.json", bundle_payload)
        write_json(runtime / "evaluations" / f"{definition.name}.json", evaluation.to_dict())
        summary["scenarios"][definition.name] = {  # type: ignore[index]
            "scenario_id": scenario_id,
            "agent_run_id": agent_run_id,
            "emitted_trace_ids": list(trace_ids),
            "discovered_trace_ids": list(trace_ids),
            "retrieved_trace_ids": [trace.trace_id for trace in traces],
            "emitted_log_ids": list(log_ids),
            "retrieved_log_ids": [log.log_id for log in logs],
            "expected_trace_count": definition.expected_trace_count,
            "actual_trace_count": len(traces),
            "expected_log_count": definition.expected_log_count,
            "actual_log_count": len(logs),
            "expected_verdict": definition.expected_verdict,
            "actual_verdict": evaluation.verdict.value,
            "expected_rule_statuses": definition.expected_rule_statuses,
            "actual_rule_statuses": actual_statuses,
            "matched_expectations": True,
            "trace_preservation_result": True,
            "log_preservation_result": True,
            "trace_preservation_details": verification.trace_details.to_dict(),
            "log_preservation_details": verification.log_details.to_dict(),
            "preservation_errors": [],
            "preservation_passed": True,
            "exact_status_match": True,
            "verdict_match": True,
            "evaluator_contract_match": True,
        }
    write_json(runtime / "environment_check.json", env)
    write_json(runtime / "scenario_catalog.json", finalize_evidence.scenario_catalogue())
    write_json(runtime / "trace_emission_manifest.json", trace_manifest)
    write_json(runtime / "log_emission_manifest.json", log_manifest)
    write_json(runtime / "gate3b_summary.json", summary)
    return summary


def result(name: str = "ok") -> VerificationCommandResult:
    command = EXPECTED_VERIFICATION_COMMANDS.get(name, ["<current-python>", "-m", "pytest"])
    test_count = 1 if name in PYTEST_VERIFICATION_NAMES else None
    return VerificationCommandResult(name, command, 0, True, test_count, "1 passed", "", "2026-01-01T00:00:00Z")


def complete_results() -> list[VerificationCommandResult]:
    return [result(name) for name in sorted(REQUIRED_VERIFICATION_NAMES)]


def test_finalizer_rejects_unknown_batch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    assert finalize_evidence.main(["--batch-id", "missing"]) == 2


def test_finalizer_rejects_partial_batch(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / VALID_BATCH_ID
    runtime.mkdir()
    summary = complete_summary()
    summary["scenario_count"] = 1
    (runtime / "gate3b_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    assert finalize_evidence.main(["--batch-id", VALID_BATCH_ID]) == 1


def test_dry_run_writes_no_evidence(monkeypatch, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", complete_results)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    monkeypatch.setattr(finalize_evidence, "validate_finalizer_provenance", clean_provenance)
    assert finalize_evidence.main(["--batch-id", VALID_BATCH_ID, "--dry-run"]) == 0
    assert not evidence.exists()


def test_successful_finalization_writes_six_files(monkeypatch, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", complete_results)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    monkeypatch.setattr(finalize_evidence, "validate_finalizer_provenance", clean_provenance)
    assert finalize_evidence.main(["--batch-id", VALID_BATCH_ID]) == 0
    files = {path.name for path in evidence.iterdir()}
    assert files == {
        "gate3b_scenario_catalog.json",
        "gate3b_log_api_contract.json",
        "gate3b_live_results.json",
        "gate3b_verification_results.json",
        "gate3b_secret_scan.json",
        "gate3b_decision.json",
    }
    decision = json.loads((evidence / "gate3b_decision.json").read_text(encoding="utf-8"))
    assert decision["gate3b_complete"] is True
    assert decision["runner_scenario_conclusion_flags_trusted_as_authoritative"] is False


def assert_rejected(summary: dict[str, object]) -> None:
    try:
        finalize_evidence.validate_completion_contract(summary)
    except (finalize_evidence.FinalizerContractError, finalize_evidence.FinalizerUsageError):
        return
    raise AssertionError("summary should have been rejected")


def first_scenario(summary: dict[str, object]) -> dict[str, object]:
    return next(iter(summary["scenarios"].values()))  # type: ignore[index,union-attr]


def assert_runtime_rejected(tmp_path: Path, summary: dict[str, object]) -> None:
    original_root = finalize_evidence.RUNTIME_ROOT
    finalize_evidence.RUNTIME_ROOT = tmp_path
    try:
        assert_rejected(summary)
    finally:
        finalize_evidence.RUNTIME_ROOT = original_root


def first_trace_file(tmp_path: Path, scenario_name: str) -> Path:
    return next((tmp_path / VALID_BATCH_ID / "retrieved_traces" / scenario_name).glob("*.normalized.json"))


def mutate_json(path: Path, mutator) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    write_json(path, payload)


def runtime_path(tmp_path: Path, *parts: str) -> Path:
    return tmp_path / VALID_BATCH_ID / Path(*parts)


def summary_path(tmp_path: Path) -> Path:
    return runtime_path(tmp_path, "gate3b_summary.json")


def update_summary_file(tmp_path: Path, mutator) -> dict[str, object]:
    payload = json.loads(summary_path(tmp_path).read_text(encoding="utf-8"))
    mutator(payload)
    write_json(summary_path(tmp_path), payload)
    return payload


def manifest_path(tmp_path: Path, filename: str) -> Path:
    return runtime_path(tmp_path, filename)


def scenario_log_name() -> str:
    return "pass_single_trace_correlated_logs"


def warning_name() -> str:
    return "pass_with_warnings_uncorrelated_logs"


def supports_symlink(tmp_path: Path) -> bool:
    target = tmp_path / "target"
    link = tmp_path / "link"
    target.write_text("x", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        return False
    return link.is_symlink()


def test_strict_batch_ids_and_summary_agreement(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    assert finalize_evidence.resolve_runtime_batch_dir(VALID_BATCH_ID) == (tmp_path / VALID_BATCH_ID).resolve()
    bad_ids = [
        ".",
        "..",
        "../batch",
        "batch/../../other",
        str((tmp_path / "absolute").resolve()),
        "C:\\batch",
        " 20260101T000000Z-abcdef123456",
        "2026011T000000Z-abcdef123456",
        "20260101T000000Z-abcdef12345",
        "20260101T000000Z-abcdef1234567",
        "20260101T000000Z-ABCDEF123456",
    ]
    for batch_id in bad_ids:
        try:
            finalize_evidence.resolve_runtime_batch_dir(batch_id)
        except finalize_evidence.FinalizerUsageError:
            pass
        else:
            raise AssertionError(f"batch ID should fail: {batch_id}")

    other_id = "20260101T000001Z-abcdef123456"
    summary = update_summary_file(tmp_path, lambda payload: payload.update({"batch_id": other_id}))
    assert summary["batch_id"] == other_id
    try:
        finalize_evidence.load_summary(VALID_BATCH_ID)
    except finalize_evidence.FinalizerUsageError:
        pass
    else:
        raise AssertionError("summary batch mismatch should fail")


def test_symlinked_runtime_artifacts_are_rejected(monkeypatch, tmp_path: Path) -> None:
    if not supports_symlink(tmp_path):
        import pytest

        pytest.skip("host does not permit symlink creation")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    cases = [
        runtime_path(tmp_path, "gate3b_summary.json"),
        runtime_path(tmp_path, "environment_check.json"),
        runtime_path(tmp_path, "trace_emission_manifest.json"),
        first_trace_file(tmp_path, scenario_log_name()),
        runtime_path(tmp_path, "retrieved_logs", f"{scenario_log_name()}.normalized.json"),
    ]
    for path in cases:
        write_complete_runtime(tmp_path, VALID_BATCH_ID)
        path.unlink()
        path.symlink_to(outside)
        assert_runtime_rejected(tmp_path, summary)

    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    shutil.rmtree(runtime_path(tmp_path, "evaluations"))
    runtime_path(tmp_path, "evaluations").symlink_to(tmp_path, target_is_directory=True)
    assert_runtime_rejected(tmp_path, summary)

    shutil.rmtree(tmp_path / VALID_BATCH_ID)
    target = tmp_path / "real-batch"
    target.mkdir()
    (tmp_path / VALID_BATCH_ID).symlink_to(target, target_is_directory=True)
    try:
        finalize_evidence.resolve_runtime_batch_dir(VALID_BATCH_ID)
    except finalize_evidence.FinalizerUsageError:
        pass
    else:
        raise AssertionError("symlinked batch directory should fail")


def test_config_snapshot_and_service_binding_are_required(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    signoz_key = "SIGNOZ" + "_API_KEY"
    cases = [
        lambda payload: payload.pop("config"),
        lambda payload: payload["config"].pop("TRACEGUARD_GATE3B_SERVICE_NAME"),
        lambda payload: payload["config"].update({"TRACEGUARD_GATE3B_SERVICE_NAME": ""}),
        lambda payload: payload["config"].update({signoz_key: "not-set"}),
        lambda payload: payload["config"].update({"TRACEGUARD_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/other"}),
        lambda payload: payload["config"].update({"SIGNOZ_REQUEST_TIMEOUT_SECONDS": True}),
        lambda payload: payload["config"].update({"TRACEGUARD_OTLP_TIMEOUT_SECONDS": 0}),
        lambda payload: payload["config"].update({"TRACEGUARD_GATE3B_POLL_INTERVAL_SECONDS": 61.0}),
    ]
    for mutator in cases:
        write_complete_runtime(tmp_path, VALID_BATCH_ID)
        summary = update_summary_file(tmp_path, mutator)
        assert_runtime_rejected(tmp_path, summary)

    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    summary = update_summary_file(tmp_path, lambda payload: payload["config"].update({"TRACEGUARD_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/traces"}))
    assert_runtime_rejected(tmp_path, summary)

    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(manifest_path(tmp_path, "trace_emission_manifest.json"), lambda payload: payload["scenarios"][scenario_log_name()].update({"service_name": "other"}))
    assert_runtime_rejected(tmp_path, json.loads(summary_path(tmp_path).read_text(encoding="utf-8")))

    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(manifest_path(tmp_path, "log_emission_manifest.json"), lambda payload: payload["scenarios"][scenario_log_name()].update({"service_name": "other"}))
    assert_runtime_rejected(tmp_path, json.loads(summary_path(tmp_path).read_text(encoding="utf-8")))

    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(manifest_path(tmp_path, "trace_emission_manifest.json"), lambda payload: payload["scenarios"][scenario_log_name()].update({"service_name": "trace-svc"}))
    mutate_json(manifest_path(tmp_path, "log_emission_manifest.json"), lambda payload: payload["scenarios"][scenario_log_name()].update({"service_name": "log-svc"}))
    assert_runtime_rejected(tmp_path, json.loads(summary_path(tmp_path).read_text(encoding="utf-8")))


def test_retrieved_log_source_provenance_is_exact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    summary = json.loads(summary_path(tmp_path).read_text(encoding="utf-8"))
    assert finalize_evidence.validate_completion_contract(summary).scenario_validations[scenario_log_name()].retrieved_log_source == "SigNoz Logs API"
    for source in (None, "", "MCP", "synthetic", "local", "unknown"):
        write_complete_runtime(tmp_path, VALID_BATCH_ID)

        def mutate(payload, source=source):
            if source is None:
                payload["logs"][0].pop("source")
            else:
                payload["logs"][0]["source"] = source

        mutate_json(runtime_path(tmp_path, "retrieved_logs", f"{scenario_log_name()}.normalized.json"), mutate)
        assert_runtime_rejected(tmp_path, json.loads(summary_path(tmp_path).read_text(encoding="utf-8")))


def test_trace_emission_manifest_internal_consistency(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    name = scenario_log_name()

    def reject(mutator) -> None:
        write_complete_runtime(tmp_path, VALID_BATCH_ID)
        mutate_json(manifest_path(tmp_path, "trace_emission_manifest.json"), lambda payload: mutator(payload["scenarios"][name]))
        assert_runtime_rejected(tmp_path, json.loads(summary_path(tmp_path).read_text(encoding="utf-8")))

    reject(lambda item: item["root_span_ids_by_trace_id"].update({"f" * 32: "1" * 16}))
    reject(lambda item: item["root_span_ids_by_trace_id"].pop(item["emitted_trace_ids"][0]))
    reject(lambda item: item["root_span_ids_by_trace_id"].update({item["emitted_trace_ids"][0]: "9" * 16}))
    reject(lambda item: item["span_ids_by_trace_id_and_name"][item["emitted_trace_ids"][0]].update({"extra.span": "4" * 16}))
    reject(lambda item: item["span_ids_by_trace_id_and_name"][item["emitted_trace_ids"][0]].pop("tool.call"))
    reject(lambda item: item["span_ids_by_trace_id_and_name"][item["emitted_trace_ids"][0]].update({"tool.call": item["span_ids_by_trace_id_and_name"][item["emitted_trace_ids"][0]]["agent.run"]}))
    reject(lambda item: item["emitted_trace_ids"].__setitem__(0, "not-hex"))
    reject(lambda item: item["span_ids_by_trace_id_and_name"][item["emitted_trace_ids"][0]].update({"tool.call": "not-span"}))
    reject(lambda item: item["parent_span_ids_by_trace_id_and_name"][item["emitted_trace_ids"][0]].update({"tool.call": None}))
    reject(lambda item: item["expected_attributes_by_trace_id_and_name"][item["emitted_trace_ids"][0]]["agent.run"].pop(TRACE_BATCH_ATTR))


def test_log_emission_manifest_internal_consistency(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    name = scenario_log_name()

    def reject(scenario_name: str, mutator) -> None:
        write_complete_runtime(tmp_path, VALID_BATCH_ID)
        mutate_json(manifest_path(tmp_path, "log_emission_manifest.json"), lambda payload: mutator(payload["scenarios"][scenario_name]))
        assert_runtime_rejected(tmp_path, json.loads(summary_path(tmp_path).read_text(encoding="utf-8")))

    reject(name, lambda item: item["expected_trace_ids"].update({"extra-log": item["expected_trace_ids"][item["log_ids"][0]]}))
    reject(name, lambda item: item["expected_trace_ids"].pop(item["log_ids"][0]))
    reject(name, lambda item: item["expected_trace_ids"].update({item["log_ids"][0]: "f" * 32}))
    reject(name, lambda item: item["expected_span_ids"].update({item["log_ids"][0]: "f" * 16}))
    reject(warning_name(), lambda item: item["expected_agent_run_ids"].update({log_id: "run-no-mismatch" for log_id in item["log_ids"]}))
    reject(name, lambda item: item["expected_agent_run_ids"].update({item["log_ids"][0]: "unexpected-mismatch"}))

    zero_name = "pass_single_trace_without_logs"
    reject(zero_name, lambda item: item["expected_trace_ids"].update({"extra": "f" * 32}))


def test_raw_artifacts_are_authoritative_over_runner_summary_flags(tmp_path: Path) -> None:
    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    name = "pass_single_trace_correlated_logs"

    mutate_json(first_trace_file(tmp_path, name), lambda payload: payload["spans"][1].update({"parent_span_id": "9" * 16}))
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(first_trace_file(tmp_path, name), lambda payload: payload["spans"][1]["attributes"].update({"tool.status": "changed"}))
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(first_trace_file(tmp_path, name), lambda payload: payload["spans"][0].update({"service_name": "other"}))
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(tmp_path / VALID_BATCH_ID / "retrieved_logs" / f"{name}.normalized.json", lambda payload: payload["logs"][0].update({"body": "changed"}))
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(tmp_path / VALID_BATCH_ID / "retrieved_logs" / f"{name}.normalized.json", lambda payload: payload["logs"][0].update({"timestamp": "not-a-date"}))
    assert_runtime_rejected(tmp_path, summary)


def test_runtime_artifact_contradictions_are_rejected(tmp_path: Path) -> None:
    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    name = "pass_single_trace_correlated_logs"
    mutate_json(tmp_path / VALID_BATCH_ID / "evaluations" / f"{name}.json", lambda payload: payload.update({"verdict": "BLOCK"}))
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(tmp_path / VALID_BATCH_ID / "run_bundles" / f"{name}.json", lambda payload: payload["traces"][0]["trace"].update({"trace_id": "f" * 32}))
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    first_trace_file(tmp_path, name).unlink()
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    (tmp_path / VALID_BATCH_ID / "retrieved_logs" / "unexpected.normalized.json").write_text("{}", encoding="utf-8")
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    first_trace_file(tmp_path, name).write_text("{", encoding="utf-8")
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    (tmp_path / VALID_BATCH_ID / "retrieved_logs" / f"{name}.normalized.json").write_text("{", encoding="utf-8")
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    trace_path = first_trace_file(tmp_path, name)
    duplicate = trace_path.with_name("f" * 32 + ".normalized.json")
    duplicate.write_text(trace_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert_runtime_rejected(tmp_path, summary)

    summary = write_complete_runtime(tmp_path, VALID_BATCH_ID)
    mutate_json(tmp_path / VALID_BATCH_ID / "retrieved_logs" / f"{name}.normalized.json", lambda payload: payload["logs"].append(dict(payload["logs"][0])))
    assert_runtime_rejected(tmp_path, summary)


def test_finalizer_recomputes_status_verdict_counts_ids_and_preservation() -> None:
    cases = []
    summary = complete_summary()
    first_scenario(summary)["actual_rule_statuses"] = first_scenario(summary)["actual_rule_statuses"] | {"TG-TEL-001": "FAILED"}  # type: ignore[operator]
    first_scenario(summary)["exact_status_match"] = True
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["actual_rule_statuses"] = {k: v for k, v in first_scenario(summary)["actual_rule_statuses"].items() if k != "TG-TEL-001"}  # type: ignore[union-attr]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["actual_rule_statuses"] = first_scenario(summary)["actual_rule_statuses"] | {"TG-UNKNOWN": "PASSED"}  # type: ignore[operator]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["actual_rule_statuses"] = first_scenario(summary)["actual_rule_statuses"] | {"TG-TEL-001": "BOGUS"}  # type: ignore[operator]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["actual_verdict"] = "BLOCK"
    first_scenario(summary)["verdict_match"] = True
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["expected_verdict"] = "BLOCK"
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["actual_trace_count"] = 99
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["actual_log_count"] = 99
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["discovered_trace_ids"] = ["c" * 32]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["retrieved_trace_ids"] = ["c" * 32]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["emitted_trace_ids"] = ["a" * 32, "a" * 32]
    cases.append(summary)

    summary = complete_summary()
    logged = [s for s in summary["scenarios"].values() if s["expected_log_count"] == 2][0]  # type: ignore[index,union-attr]
    logged["retrieved_log_ids"] = ["other-1", "other-2"]
    cases.append(summary)

    summary = complete_summary()
    logged = [s for s in summary["scenarios"].values() if s["expected_log_count"] == 2][0]  # type: ignore[index,union-attr]
    logged["retrieved_log_ids"] = ["dup", "dup"]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["trace_preservation_details"]["span_ids_match"] = False  # type: ignore[index]
    cases.append(summary)

    summary = complete_summary()
    first_scenario(summary)["preservation_errors"] = ["bad"]
    cases.append(summary)

    summary = complete_summary()
    summary["environment_check"]["authenticated_trace_api_access"] = False  # type: ignore[index]
    cases.append(summary)

    summary = complete_summary()
    summary["environment_check"]["trace_otlp_endpoint"] = "ftp://localhost/traces"  # type: ignore[index]
    cases.append(summary)

    summary = complete_summary()
    summary["matched_count"] = 3
    cases.append(summary)

    summary = complete_summary()
    summary["failed_count"] = 1
    cases.append(summary)

    summary = complete_summary()
    summary["all_expectations_matched"] = False
    cases.append(summary)

    for case in cases:
        assert_rejected(case)


def test_exact_verification_command_set_is_required(monkeypatch, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "validate_finalizer_provenance", clean_provenance)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})

    bad_sets = [
        [item for item in complete_results() if item.name != "gate3b_tests"],
        [item for item in complete_results() if item.name != "full_suite"],
        complete_results() + [result("gate3b_tests")],
        complete_results() + [result("unknown")],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 1, False, 1, "", "", "2026-01-01T00:00:00Z") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 1, True, 1, "", "", "2026-01-01T00:00:00Z") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 0, False, 1, "", "", "2026-01-01T00:00:00Z") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, ["<current-python>", "-m", "pytest"], 0, True, 1, "", "", "2026-01-01T00:00:00Z") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 0, True, None, "", "", "2026-01-01T00:00:00Z") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 0, True, 0, "", "", "2026-01-01T00:00:00Z") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 0, True, 1, "", "", "now") for item in complete_results()],
        [result("gate3b_tests")],
    ]
    for bad in bad_sets:
        monkeypatch.setattr(finalize_evidence, "run_verification_commands", lambda bad=bad: bad)
        assert finalize_evidence.main(["--batch-id", VALID_BATCH_ID]) == 3
        assert not evidence.exists()


def test_secret_allowlist_is_exact_and_findings_are_redacted() -> None:
    clean = "\n".join([
        "SIGNOZ_API_KEY=<redacted>",
        "Bearer fake-token",
        "password='changeme-for-local-testing'",
    ])
    assert finalize_evidence.scan_text_payload_for_secrets("x", clean) == []
    dirty = "\n".join([
        "fake but real Bearer " + "liveabcdefghijklmnop",
        "README example SIGNOZ_API_KEY=" + "live-real-key",
        "secret password='" + "super-real-password" + "'",
        "-----BEGIN " + "PRIVATE KEY-----",
    ])
    findings = finalize_evidence.scan_text_payload_for_secrets("tests/example.txt", dirty)
    assert {item["category"] for item in findings} == {"bearer_token", "signoz_api_key", "password_assignment", "private_key"}
    assert all("live" not in json.dumps(item) and "password" not in json.dumps(item.get("value", "")) for item in findings)


def test_secret_scanner_detects_regex_like_secret_values_without_leaking_values() -> None:
    values = [
        "abc" + "[def]" + "ghi123",
        "abc" + "(def)" + "ghi123",
        "abc" + "\\def\\ghi123",
        "abc" + "{def}" + "ghi123",
        "abc" + ".def-ghi_123!",
    ]
    for value in values:
        findings = finalize_evidence.scan_text_payload_for_secrets("tests/example.txt", "SIGNOZ_API_KEY=" + value)
        assert findings == [{"path": "tests/example.txt", "line": 1, "category": "signoz_api_key"}]
        assert value not in json.dumps(findings)


def test_secret_scanner_detects_json_and_colon_credential_forms() -> None:
    signoz_key = "SIGNOZ" + "_API_KEY"
    signoz_header = "SIGNOZ" + "-API-KEY"
    signoz_lower = "signoz" + "_api_key"
    auth = "Authori" + "zation"
    cookie = "Cook" + "ie"
    password = "pass" + "word"
    forms = {
        "env": signoz_key + "=" + "live-value",
        "yaml": signoz_key + ": " + "live-value",
        "json_api": '"' + signoz_key + '": "' + "live-value" + '"',
        "json_header": '"' + signoz_header + '": "' + "live-value" + '"',
        "json_lower": '"' + signoz_lower + '": "' + "live-value" + '"',
        "auth_header": auth + ": Bearer " + "live-value",
        "json_auth": '"' + auth + '": "Bearer ' + "live-value" + '"',
        "json_auth_lower": '"' + auth.lower() + '": "' + "live-value" + '"',
        "cookie": cookie + ": session=" + "live-value",
        "json_cookie": '"' + cookie + '": "session=' + "live-value" + '"',
        "password_assignment": password + '="' + "live-value" + '"',
        "password_colon": password + ': "' + "live-value" + '"',
        "json_password": '"' + password + '": "' + "live-value" + '"',
    }
    for label, text in forms.items():
        findings = finalize_evidence.scan_text_payload_for_secrets(f"{label}.txt", text)
        assert len(findings) == 1
        assert "live-value" not in json.dumps(findings)


def test_secret_scanner_safe_placeholders_across_supported_syntaxes() -> None:
    signoz_key = "SIGNOZ" + "_API_KEY"
    signoz_header = "SIGNOZ" + "-API-KEY"
    signoz_lower = "signoz" + "_api_key"
    auth = "Authori" + "zation"
    cookie = "Cook" + "ie"
    password = "pass" + "word"
    clean = "\n".join(
        [
            signoz_key + "=<redacted>",
            signoz_key + ": <set>",
            '"' + signoz_key + '": "example-key"',
            '"' + signoz_header + '": "fake-key"',
            '"' + signoz_lower + '": "test-api-key"',
            auth + ": Bearer fake-token",
            '"' + auth + '": "Bearer synthetic-token"',
            cookie + ": <redacted>",
            '"' + cookie + '": "<redacted>"',
            password + '="changeme-for-local-testing"',
            '"' + password + '": "changeme-for-local-testing"',
        ]
    )
    assert finalize_evidence.scan_text_payload_for_secrets("placeholders.txt", clean) == []


def test_secret_scanner_keywords_do_not_create_exemptions_and_cookie_is_line_bounded() -> None:
    signoz_key = "SIGNOZ" + "_API_KEY"
    cookie = "Cook" + "ie"
    text = "\n".join(
        [
            signoz_key + "=" + "example-but-real",
            signoz_key + "=" + "fake-but-real",
            signoz_key + "=" + "secret-but-real",
            cookie + ": session=" + "cookie-real",
            "ordinary text",
        ]
    )
    findings = finalize_evidence.scan_text_payload_for_secrets("keywords.txt", text)
    assert [item["line"] for item in findings] == [1, 2, 3, 4]
    assert all("real" not in json.dumps(item) for item in findings)


def test_scanner_source_declarations_do_not_create_false_positive() -> None:
    line = '("signoz_api_key", re.compile(r"\\bSIGNOZ_API_KEY\\s*=\\s*[\'\\"]?([^\'\\"\\s#]+)")),'
    assert finalize_evidence.scan_text_payload_for_secrets("gate3b/finalize_evidence.py", line) == []


def test_git_provenance_validation(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs.get("shell")))
        if command[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(command, 0, stdout=str(finalize_evidence.REPO_ROOT) + "\n", stderr="")
        if command[-1] == "HEAD":
            return subprocess.CompletedProcess(command, 0, stdout=SOURCE_SHA + "\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(finalize_evidence.subprocess, "run", fake_run)
    provenance = finalize_evidence.current_git_provenance()
    assert provenance.source_commit_sha == SOURCE_SHA
    assert provenance.source_worktree_clean is True
    assert all(command[0] == "git" and shell is False for command, shell in calls)


def test_git_provenance_rejects_bad_states(monkeypatch) -> None:
    def install(stdout_by_suffix: dict[str, tuple[int, str]]) -> None:
        def fake_run(command, **kwargs):
            suffix = command[-1]
            code, stdout = stdout_by_suffix.get(suffix, (0, ""))
            return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")

        monkeypatch.setattr(finalize_evidence.subprocess, "run", fake_run)

    install({"--show-toplevel": (0, str(finalize_evidence.REPO_ROOT.parent) + "\n"), "HEAD": (0, SOURCE_SHA + "\n"), "--porcelain": (0, "")})
    try:
        finalize_evidence.current_git_provenance()
    except finalize_evidence.FinalizerContractError:
        pass
    else:
        raise AssertionError("wrong repository root should fail")

    install({"--show-toplevel": (0, str(finalize_evidence.REPO_ROOT) + "\n"), "HEAD": (0, "bad\n"), "--porcelain": (0, "")})
    try:
        finalize_evidence.current_git_provenance()
    except finalize_evidence.FinalizerContractError:
        pass
    else:
        raise AssertionError("malformed HEAD should fail")

    install({"--show-toplevel": (0, str(finalize_evidence.REPO_ROOT) + "\n"), "HEAD": (0, SOURCE_SHA + "\n"), "--porcelain": (0, " M file\n")})
    summary = {"source_commit_sha": SOURCE_SHA, "source_worktree_clean": True}
    try:
        finalize_evidence.validate_finalizer_provenance(summary)
    except finalize_evidence.FinalizerContractError:
        pass
    else:
        raise AssertionError("dirty worktree should fail")

    install({"--show-toplevel": (0, str(finalize_evidence.REPO_ROOT) + "\n"), "HEAD": (0, SOURCE_SHA + "\n"), "--porcelain": (0, "")})
    try:
        finalize_evidence.validate_finalizer_provenance({"source_commit_sha": "2" * 40, "source_worktree_clean": True})
    except finalize_evidence.FinalizerContractError:
        pass
    else:
        raise AssertionError("runtime/finalizer SHA mismatch should fail")

    install({"--show-toplevel": (1, ""), "HEAD": (0, SOURCE_SHA + "\n"), "--porcelain": (0, "")})
    try:
        finalize_evidence.current_git_provenance()
    except finalize_evidence.FinalizerContractError:
        pass
    else:
        raise AssertionError("git command failure should fail")


def test_serialized_evidence_bytes_are_the_bytes_written(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence_dir)
    payloads = {f"file-{index}.json": {"index": index, "sanitized": True} for index in range(6)}
    serialized = finalize_evidence.serialize_evidence(payloads)
    assert serialized.scan_result["passed"] is True
    finalize_evidence.write_serialized_evidence(serialized)
    for name, text in serialized.serialized_text.items():
        assert (evidence_dir / name).read_text(encoding="utf-8") == text


def test_prepare_failure_leaves_previous_evidence_intact(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    previous = {f"file-{index}.json": "previous\n" for index in range(6)}
    for name, text in previous.items():
        (evidence_dir / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence_dir)
    payloads = {name: {"index": index, "sanitized": True} for index, name in enumerate(previous)}
    serialized = finalize_evidence.serialize_evidence(payloads)
    calls = {"count": 0}
    original_fsync = finalize_evidence.os.fsync

    def fail_second_fsync(fd: int) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("prepare failed")
        original_fsync(fd)

    monkeypatch.setattr(finalize_evidence.os, "fsync", fail_second_fsync)
    try:
        finalize_evidence.write_serialized_evidence(serialized)
    except OSError:
        pass
    else:
        raise AssertionError("expected write preparation failure")
    for name, text in previous.items():
        assert (evidence_dir / name).read_text(encoding="utf-8") == text


def assert_no_publication_scratch(evidence_dir: Path) -> None:
    assert not list(evidence_dir.glob(".tmp-*"))
    assert not list(evidence_dir.glob(".bak-*"))


def test_backup_failure_rolls_back_existing_files(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    previous = {f"file-{index}.json": "previous\n" for index in range(6)}
    for name, text in previous.items():
        (evidence_dir / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence_dir)
    serialized = finalize_evidence.serialize_evidence({name: {"new": name, "sanitized": True} for name in previous})
    original_replace = Path.replace
    calls = {"count": 0}

    def fail_first_backup(self: Path, target: Path):
        if self.name in previous and target.name.startswith(".bak-"):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("backup failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_first_backup)
    try:
        finalize_evidence.write_serialized_evidence(serialized)
    except OSError:
        pass
    else:
        raise AssertionError("expected backup failure")
    for name, text in previous.items():
        assert (evidence_dir / name).read_text(encoding="utf-8") == text
    assert_no_publication_scratch(evidence_dir)


def test_replacement_failures_restore_existing_and_remove_new_targets(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    previous = {f"file-{index}.json": "previous\n" for index in range(3)}
    payloads = {f"file-{index}.json": {"index": index, "sanitized": True} for index in range(6)}
    for name, text in previous.items():
        (evidence_dir / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence_dir)
    serialized = finalize_evidence.serialize_evidence(payloads)
    original_replace = Path.replace

    for fail_on in (1, 4):
        for path in evidence_dir.iterdir():
            path.unlink()
        for name, text in previous.items():
            (evidence_dir / name).write_text(text, encoding="utf-8")
        calls = {"count": 0}

        def fail_replacement(self: Path, target: Path, *, fail_on=fail_on):
            if self.name.startswith(".tmp-"):
                calls["count"] += 1
                if calls["count"] == fail_on:
                    raise OSError("replace failed")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", fail_replacement)
        try:
            finalize_evidence.write_serialized_evidence(serialized)
        except OSError:
            pass
        else:
            raise AssertionError("expected replacement failure")
        for name, text in previous.items():
            assert (evidence_dir / name).read_text(encoding="utf-8") == text
        for index in range(3, 6):
            assert not (evidence_dir / f"file-{index}.json").exists()
        assert_no_publication_scratch(evidence_dir)


def test_proposed_evidence_scan_blocks_publication(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    write_complete_runtime(tmp_path, VALID_BATCH_ID)
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence_dir)
    monkeypatch.setattr(finalize_evidence, "validate_finalizer_provenance", clean_provenance)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", complete_results)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    original = finalize_evidence.build_evidence

    def poisoned(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload["gate3b_live_results.json"]["leak"] = "Bearer " + "liveabcdefghijklmnop"
        return payload

    monkeypatch.setattr(finalize_evidence, "build_evidence", poisoned)
    assert finalize_evidence.main(["--batch-id", VALID_BATCH_ID]) == 3
    assert not evidence_dir.exists()
