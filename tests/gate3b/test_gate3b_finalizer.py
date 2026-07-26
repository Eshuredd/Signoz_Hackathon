from __future__ import annotations

import json
from pathlib import Path

from gate3b import finalize_evidence
from gate3b.finalize_evidence import REQUIRED_VERIFICATION_NAMES, VerificationCommandResult
from gate3b.scenarios import SCENARIO_DEFINITIONS


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
        "batch_id": "batch-ok",
        "captured_at": "2026-01-01T00:00:00Z",
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


def result(name: str = "ok") -> VerificationCommandResult:
    return VerificationCommandResult(name, ["<current-python>", "-m", "pytest"], 0, True, 1, "1 passed", "", "now")


def complete_results() -> list[VerificationCommandResult]:
    return [result(name) for name in sorted(REQUIRED_VERIFICATION_NAMES)]


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
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", complete_results)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    assert finalize_evidence.main(["--batch-id", "batch", "--dry-run"]) == 0
    assert not evidence.exists()


def test_successful_finalization_writes_six_files(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "batch"
    evidence = tmp_path / "evidence"
    runtime.mkdir()
    (runtime / "gate3b_summary.json").write_text(json.dumps(complete_summary()), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", complete_results)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})
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
    assert decision["runner_flags_trusted_as_authoritative"] is False


def assert_rejected(summary: dict[str, object]) -> None:
    try:
        finalize_evidence.validate_completion_contract(summary)
    except finalize_evidence.FinalizerContractError:
        return
    raise AssertionError("summary should have been rejected")


def first_scenario(summary: dict[str, object]) -> dict[str, object]:
    return next(iter(summary["scenarios"].values()))  # type: ignore[index,union-attr]


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
    runtime = tmp_path / "batch"
    evidence = tmp_path / "evidence"
    runtime.mkdir()
    (runtime / "gate3b_summary.json").write_text(json.dumps(complete_summary()), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})

    bad_sets = [
        [item for item in complete_results() if item.name != "gate3b_tests"],
        [item for item in complete_results() if item.name != "full_suite"],
        complete_results() + [result("gate3b_tests")],
        complete_results() + [result("unknown")],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 1, False, None, "", "", "now") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 1, True, None, "", "", "now") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, item.command, 0, False, None, "", "", "now") for item in complete_results()],
        [result(item.name) if item.name != "gate3b_tests" else VerificationCommandResult(item.name, [], 0, True, None, "", "", "now") for item in complete_results()],
        [result("gate3b_tests")],
    ]
    for bad in bad_sets:
        monkeypatch.setattr(finalize_evidence, "run_verification_commands", lambda bad=bad: bad)
        assert finalize_evidence.main(["--batch-id", "batch"]) == 3
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


def test_proposed_evidence_scan_blocks_publication(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "batch"
    evidence_dir = tmp_path / "evidence"
    runtime.mkdir()
    (runtime / "gate3b_summary.json").write_text(json.dumps(complete_summary()), encoding="utf-8")
    monkeypatch.setattr(finalize_evidence, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(finalize_evidence, "EVIDENCE_ROOT", evidence_dir)
    monkeypatch.setattr(finalize_evidence, "run_verification_commands", complete_results)
    monkeypatch.setattr(finalize_evidence, "run_secret_scan", lambda: {"tracked_files_scanned": 1, "tracked_findings_count": 0, "findings": [], "passed": True, "sanitized": True})
    original = finalize_evidence.build_evidence

    def poisoned(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload["gate3b_live_results.json"]["leak"] = "Bearer " + "liveabcdefghijklmnop"
        return payload

    monkeypatch.setattr(finalize_evidence, "build_evidence", poisoned)
    assert finalize_evidence.main(["--batch-id", "batch"]) == 3
    assert not evidence_dir.exists()
