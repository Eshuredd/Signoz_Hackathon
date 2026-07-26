from __future__ import annotations

import json
from pathlib import Path

from gate3b import finalize_evidence
from gate3b.finalize_evidence import VerificationCommandResult
from gate3b.scenarios import SCENARIO_DEFINITIONS


def complete_summary() -> dict[str, object]:
    scenarios = {}
    for definition in SCENARIO_DEFINITIONS:
        scenarios[definition.name] = {
            "emitted_trace_ids": ["a" * 32],
            "retrieved_trace_ids": ["a" * 32],
            "expected_verdict": definition.expected_verdict,
            "actual_verdict": definition.expected_verdict,
            "expected_rule_statuses": definition.expected_rule_statuses,
            "actual_rule_statuses": definition.expected_rule_statuses,
            "matched_expectations": True,
            "trace_preservation_result": True,
            "log_preservation_result": True,
            "exact_status_match": True,
            "verdict_match": True,
            "evaluator_contract_match": True,
        }
    return {
        "batch_id": "batch-ok",
        "captured_at": "2026-01-01T00:00:00Z",
        "scenario_count": 4,
        "completed_count": 4,
        "matched_count": 4,
        "failed_count": 0,
        "all_expectations_matched": True,
        "live_exit_code": 0,
        "environment_check": {"signoz_version": "test", "trace_otlp_endpoint": "http://trace", "log_otlp_endpoint": "http://log"},
        "scenarios": scenarios,
    }


def result(name: str = "ok") -> VerificationCommandResult:
    return VerificationCommandResult(name, ["<current-python>", "-m", "pytest"], 0, True, 1, "1 passed", "", "now")


def test_finalizer_rejects_unknown_batch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    assert finalize_evidence.main(["--batch-id", "missing"]) == 2


def test_finalizer_rejects_partial_batch(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "batch" 
    runtime.mkdir()
    summary = complete_summary()
    summary["scenario_count"] = 1
    (runtime / "gate3b_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    assert finalize_evidence.main(["--batch-id", "batch"]) == 1


def test_dry_run_writes_no_evidence(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "batch"
    evidence = tmp_path / "evidence"
    runtime.mkdir()
    (runtime / "gate3b_summary.json").write_text(json.dumps(complete_summary()), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", lambda: [result()])
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"scanned_tracked_file_count": 1, "findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    assert finalize_evidence.main(["--batch-id", "batch", "--dry-run"]) == 0
    assert not evidence.exists()


def test_successful_finalization_writes_six_files(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "batch"
    evidence = tmp_path / "evidence"
    runtime.mkdir()
    (runtime / "gate3b_summary.json").write_text(json.dumps(complete_summary()), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", lambda: [result("gate3b_tests")])
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"scanned_tracked_file_count": 1, "findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    assert finalize_evidence.main(["--batch-id", "batch"]) == 0
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
