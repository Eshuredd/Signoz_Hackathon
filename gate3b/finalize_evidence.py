from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate2.models import Source, Span, Trace
from gate3.evaluator import evaluate_run_bundle
from gate3.models import RULESET_VERSION, RuleStatus, Verdict, verdict_from_rule_results
from gate3.rules import RULE_BY_ID
from gate3.trace_loader import load_run_bundle_payload
from gate3b.bridge import build_gate3_run_bundle
from gate3b.log_api_adapter import log_api_contract
from gate3b.models import AUTHORITATIVE_LOG_SOURCE, AUTHORITATIVE_TRACE_SOURCE, LOG_ID_ATTR, RULESET_VERSION as GATE3B_RULESET_VERSION, LogEmissionResult, RetrievedLog, RuntimeScenario, TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, TraceEmissionResult, now_iso
from gate3b.otel_log_compat import compatibility_contract
from gate3b.scenarios import SCENARIO_DEFINITIONS, scenario_catalogue
from gate3b.verification import verify_preservation


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / ".traceguard" / "runtime" / "gate3b"
EVIDENCE_ROOT = REPO_ROOT / "gate3b" / "evidence"
EXPECTED_SCENARIOS = {item.name for item in SCENARIO_DEFINITIONS}
SCENARIO_BY_NAME = {item.name: item for item in SCENARIO_DEFINITIONS}
EXPECTED_RULE_IDS = set(RULE_BY_ID)
REQUIRED_VERIFICATION_NAMES = {name for name, _args in VERIFICATION_COMMANDS} if "VERIFICATION_COMMANDS" in globals() else set()
PLACEHOLDER_RE = re.compile(r"recorded in completion report", re.IGNORECASE)
PYTEST_COUNT_RE = re.compile(r"(?P<count>\d+)\s+passed\b")


@dataclass(frozen=True)
class VerificationCommandResult:
    name: str
    command: list[str]
    exit_code: int
    passed: bool
    test_count: int | None
    stdout_summary: str
    stderr_summary: str
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "test_count": self.test_count,
            "stdout_summary": self.stdout_summary,
            "stderr_summary": self.stderr_summary,
            "captured_at": self.captured_at,
        }


VERIFICATION_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("pip_check", ["-m", "pip", "check"]),
    ("compileall", ["-m", "compileall", "gate1", "gate2", "gate3", "gate3_preflight", "gate3b", "traceguard_runtime", "tests"]),
    ("gate3b_tests", ["-m", "pytest", "tests/gate3b", "-v", "--basetemp", ".test-tmp-gate3b-finalize", "-p", "no:cacheprovider"]),
    ("gate3_tests", ["-m", "pytest", "tests/gate3", "-v", "--basetemp", ".test-tmp-gate3-finalize", "-p", "no:cacheprovider"]),
    ("gate3_preflight_tests", ["-m", "pytest", "tests/gate3_preflight", "-v", "--basetemp", ".test-tmp-gate3-preflight-finalize", "-p", "no:cacheprovider"]),
    ("gate2_tests", ["-m", "pytest", "tests/gate2", "-v", "--basetemp", ".test-tmp-gate2-finalize", "-p", "no:cacheprovider"]),
    ("gate1_tests", ["-m", "pytest", "tests/gate1", "-v", "--basetemp", ".test-tmp-gate1-finalize", "-p", "no:cacheprovider"]),
    ("runtime_tests", ["-m", "pytest", "tests/runtime", "-v", "--basetemp", ".test-tmp-runtime-finalize", "-p", "no:cacheprovider"]),
    ("full_suite", ["-m", "pytest", "-v", "--basetemp", ".test-tmp-all-finalize", "-p", "no:cacheprovider"]),
    ("gate3_validate_fixtures", ["gate3/cli.py", "validate-fixtures"]),
    ("gate3_evaluate_all", ["gate3/cli.py", "evaluate-all"]),
)
REQUIRED_VERIFICATION_NAMES = {name for name, _args in VERIFICATION_COMMANDS}
EXPECTED_VERIFICATION_COMMANDS = {name: display for name, args in VERIFICATION_COMMANDS if (display := ["<current-python>", *args])}
PYTEST_VERIFICATION_NAMES = {name for name, args in VERIFICATION_COMMANDS if len(args) >= 2 and args[:2] == ["-m", "pytest"]}


@dataclass(frozen=True)
class ScenarioRuntimeValidation:
    name: str
    scenario_id: str
    agent_run_id: str
    emitted_trace_ids: tuple[str, ...]
    discovered_trace_ids: tuple[str, ...]
    retrieved_trace_ids: tuple[str, ...]
    emitted_log_ids: tuple[str, ...]
    retrieved_log_ids: tuple[str, ...]
    trace_count: int
    log_count: int
    actual_rule_statuses: dict[str, str]
    actual_verdict: str
    trace_preservation_details: dict[str, Any]
    log_preservation_details: dict[str, Any]
    preservation_passed: bool
    matched_expectations: bool


@dataclass(frozen=True)
class RuntimeBatchValidation:
    environment_check: dict[str, Any]
    scenario_validations: dict[str, ScenarioRuntimeValidation]
    recomputed_completed_count: int
    recomputed_matched_count: int
    recomputed_failed_count: int
    recomputed_all_expectations_matched: bool

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "environment_check": self.environment_check,
            "scenario_validations": {
                name: {
                    "scenario_id": item.scenario_id,
                    "agent_run_id": item.agent_run_id,
                    "emitted_trace_ids": list(item.emitted_trace_ids),
                    "discovered_trace_ids": list(item.discovered_trace_ids),
                    "retrieved_trace_ids": list(item.retrieved_trace_ids),
                    "emitted_log_ids": list(item.emitted_log_ids),
                    "retrieved_log_ids": list(item.retrieved_log_ids),
                    "trace_count": item.trace_count,
                    "log_count": item.log_count,
                    "actual_rule_statuses": item.actual_rule_statuses,
                    "actual_verdict": item.actual_verdict,
                    "trace_preservation_details": item.trace_preservation_details,
                    "log_preservation_details": item.log_preservation_details,
                    "preservation_passed": item.preservation_passed,
                    "matched_expectations": item.matched_expectations,
                }
                for name, item in sorted(self.scenario_validations.items())
            },
            "recomputed_completed_count": self.recomputed_completed_count,
            "recomputed_matched_count": self.recomputed_matched_count,
            "recomputed_failed_count": self.recomputed_failed_count,
            "recomputed_all_expectations_matched": self.recomputed_all_expectations_matched,
        }


@dataclass(frozen=True)
class SerializedEvidenceSet:
    payloads: dict[str, dict[str, Any]]
    serialized_text: dict[str, str]
    filenames: tuple[str, ...]
    scan_result: dict[str, Any]


@dataclass(frozen=True)
class ValidatedGate3BCompletion:
    batch_id: str
    environment_verified: bool
    scenario_count: int
    all_scenarios_matched: bool
    all_status_maps_matched: bool
    all_verdicts_matched: bool
    all_trace_ids_matched: bool
    all_log_ids_matched: bool
    full_preservation_verified: bool
    exact_verification_command_set: bool
    all_tests_passed: bool
    tracked_secret_scan_passed: bool
    proposed_evidence_scan_passed: bool
    live_exit_code: int
    finalizer_exit_code: int = 0

    @property
    def gate3b_complete(self) -> bool:
        return all(
            (
                self.environment_verified,
                self.scenario_count == 4,
                self.all_scenarios_matched,
                self.all_status_maps_matched,
                self.all_verdicts_matched,
                self.all_trace_ids_matched,
                self.all_log_ids_matched,
                self.full_preservation_verified,
                self.exact_verification_command_set,
                self.all_tests_passed,
                self.tracked_secret_scan_passed,
                self.proposed_evidence_scan_passed,
                self.live_exit_code == 0,
                self.finalizer_exit_code == 0,
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize sanitized committed Gate 3B evidence from a complete runtime batch.")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        summary = load_summary(args.batch_id)
        runtime_validation = validate_completion_contract(summary)
        command_results = run_verification_commands()
        try:
            validate_verification_results(command_results)
        except FinalizerContractError as exc:
            print(json.dumps({"error": "verification_command_failed", "message": sanitize_text(str(exc)), "sanitized": True}, indent=2, sort_keys=True))
            return 3
        secret_scan = run_secret_scan()
        if not secret_scan["passed"]:
            print(json.dumps({"error": "tracked_secret_scan_failed", "findings": secret_scan["findings"], "sanitized": True}, indent=2, sort_keys=True))
            return 3
        completion = ValidatedGate3BCompletion(
            batch_id=str(summary["batch_id"]),
            environment_verified=True,
            scenario_count=runtime_validation.recomputed_completed_count,
            all_scenarios_matched=True,
            all_status_maps_matched=True,
            all_verdicts_matched=True,
            all_trace_ids_matched=True,
            all_log_ids_matched=True,
            full_preservation_verified=True,
            exact_verification_command_set=True,
            all_tests_passed=True,
            tracked_secret_scan_passed=True,
            proposed_evidence_scan_passed=False,
            live_exit_code=int(summary["live_exit_code"]),
        )
        completion = replace(completion, proposed_evidence_scan_passed=True)
        expected_secret_scan = merge_secret_scans(
            secret_scan,
            {
                "proposed_evidence_files_scanned": 6,
                "proposed_evidence_findings_count": 0,
                "findings": [],
                "passed": True,
                "sanitized": True,
            },
        )
        evidence = build_evidence(summary, runtime_validation, command_results, expected_secret_scan, completion)
        if contains_placeholder(evidence):
            print(json.dumps({"error": "placeholder_evidence_detected", "sanitized": True}, indent=2, sort_keys=True))
            return 4
        serialized = serialize_evidence(evidence)
        if serialized.scan_result["proposed_evidence_files_scanned"] != 6:
            raise FinalizerContractError("proposed evidence file count changed after serialization")
        if not serialized.scan_result["passed"]:
            print(json.dumps({"error": "proposed_evidence_secret_scan_failed", "findings": serialized.scan_result["findings"], "sanitized": True}, indent=2, sort_keys=True))
            return 3
        if args.dry_run:
            print(json.dumps({"dry_run": True, "would_write": sorted(serialized.filenames), "evidence": evidence, "sanitized": True}, indent=2, sort_keys=True, default=str))
            return 0
        write_serialized_evidence(serialized)
        print(json.dumps({"finalized": True, "batch_id": args.batch_id, "files": sorted(serialized.filenames), "sanitized": True}, indent=2, sort_keys=True))
        return 0
    except FinalizerContractError as exc:
        print(json.dumps({"error": "completion_contract_failed", "message": sanitize_text(str(exc)), "sanitized": True}, indent=2, sort_keys=True))
        return 1
    except FinalizerUsageError as exc:
        print(json.dumps({"error": "invalid_finalizer_input", "message": sanitize_text(str(exc)), "sanitized": True}, indent=2, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({"error": "internal_finalizer_error", "type": exc.__class__.__name__, "message": sanitize_text(str(exc)), "sanitized": True}, indent=2, sort_keys=True))
        return 4


class FinalizerUsageError(Exception):
    pass


class FinalizerContractError(Exception):
    pass


def load_summary(batch_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", batch_id):
        raise FinalizerUsageError("batch ID contains unsupported characters")
    path = RUNTIME_ROOT / batch_id / "gate3b_summary.json"
    if not path.exists():
        raise FinalizerUsageError(f"runtime summary not found for batch {batch_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalizerUsageError("runtime summary is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FinalizerUsageError("runtime summary must be a JSON object")
    return payload


def validate_completion_contract(summary: dict[str, Any]) -> RuntimeBatchValidation:
    if summary.get("live_exit_code") != 0:
        raise FinalizerContractError("live_exit_code must be 0")
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != EXPECTED_SCENARIOS:
        raise FinalizerContractError("summary must contain the four exact Gate 3B scenarios")
    batch_id = require_non_empty_string("summary.batch_id", summary.get("batch_id"))
    runtime_dir = RUNTIME_ROOT / batch_id
    validate_expected_artifact_layout(runtime_dir)
    stored_summary = load_json_artifact(runtime_dir / "gate3b_summary.json", "runtime summary")
    if stored_summary != summary:
        raise FinalizerContractError("loaded runtime summary changed during validation")
    env = load_json_artifact(runtime_dir / "environment_check.json", "environment_check")
    if not isinstance(env, dict):
        raise FinalizerContractError("environment_check artifact must be an object")
    if env != summary.get("environment_check"):
        raise FinalizerContractError("environment_check artifact disagrees with summary")
    validate_environment(env)
    catalogue = load_json_artifact(runtime_dir / "scenario_catalog.json", "scenario_catalog")
    if catalogue != scenario_catalogue():
        raise FinalizerContractError("scenario_catalog artifact differs from immutable Gate 3B catalogue")
    trace_manifest = require_manifest(runtime_dir / "trace_emission_manifest.json", batch_id, "trace emission manifest")
    log_manifest = require_manifest(runtime_dir / "log_emission_manifest.json", batch_id, "log emission manifest")
    scenario_validations: dict[str, ScenarioRuntimeValidation] = {}
    for name, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            raise FinalizerContractError(f"{name} scenario summary must be an object")
        scenario_validations[name] = recompute_scenario_validation(
            runtime_dir,
            batch_id,
            name,
            scenario,
            trace_manifest,
            log_manifest,
        )
        compare_scenario_summary(name, scenario, scenario_validations[name])
    recomputed_completed_count = len(scenario_validations)
    recomputed_matched_count = sum(1 for item in scenario_validations.values() if item.matched_expectations)
    recomputed_failed_count = recomputed_completed_count - recomputed_matched_count
    recomputed_all_expectations_matched = recomputed_completed_count == 4 and recomputed_matched_count == 4 and recomputed_failed_count == 0
    expected_summary = {
        "scenario_count": 4,
        "completed_count": recomputed_completed_count,
        "matched_count": recomputed_matched_count,
        "failed_count": recomputed_failed_count,
        "all_expectations_matched": recomputed_all_expectations_matched,
    }
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            raise FinalizerContractError(f"{key} disagrees with finalizer recomputation")
    if GATE3B_RULESET_VERSION != "traceguard-telemetry-v2" or RULESET_VERSION != GATE3B_RULESET_VERSION:
        raise FinalizerContractError("Gate 3B ruleset version mismatch")
    return RuntimeBatchValidation(
        environment_check=env,
        scenario_validations=scenario_validations,
        recomputed_completed_count=recomputed_completed_count,
        recomputed_matched_count=recomputed_matched_count,
        recomputed_failed_count=recomputed_failed_count,
        recomputed_all_expectations_matched=recomputed_all_expectations_matched,
    )


def validate_expected_artifact_layout(runtime_dir: Path) -> None:
    expected_files = {
        runtime_dir / "gate3b_summary.json",
        runtime_dir / "environment_check.json",
        runtime_dir / "trace_emission_manifest.json",
        runtime_dir / "log_emission_manifest.json",
        runtime_dir / "scenario_catalog.json",
    }
    for path in expected_files:
        if not path.exists():
            raise FinalizerContractError(f"required runtime artifact missing: {path.name}")
    expected_scenario_files = {f"{name}.json" for name in EXPECTED_SCENARIOS}
    for subdir in ("run_bundles", "evaluations", "verification"):
        directory = runtime_dir / subdir
        if not directory.is_dir():
            raise FinalizerContractError(f"required runtime artifact directory missing: {subdir}")
        actual = {path.name for path in directory.glob("*.json")}
        if actual != expected_scenario_files:
            raise FinalizerContractError(f"{subdir} must contain exactly the four scenario JSON artifacts")
    logs_dir = runtime_dir / "retrieved_logs"
    if not logs_dir.is_dir():
        raise FinalizerContractError("required retrieved_logs directory missing")
    if {path.name for path in logs_dir.glob("*.normalized.json")} != {f"{name}.normalized.json" for name in EXPECTED_SCENARIOS}:
        raise FinalizerContractError("retrieved_logs must contain exactly the four scenario normalized artifacts")
    traces_dir = runtime_dir / "retrieved_traces"
    if not traces_dir.is_dir():
        raise FinalizerContractError("required retrieved_traces directory missing")
    if {path.name for path in traces_dir.iterdir() if path.is_dir()} != EXPECTED_SCENARIOS:
        raise FinalizerContractError("retrieved_traces must contain exactly the four scenario directories")


def load_json_artifact(path: Path, label: str) -> Any:
    if not path.exists():
        raise FinalizerContractError(f"{label} is missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalizerContractError(f"{label} is malformed JSON") from exc


def require_manifest(path: Path, batch_id: str, label: str) -> dict[str, Any]:
    payload = load_json_artifact(path, label)
    if not isinstance(payload, dict) or payload.get("batch_id") != batch_id or payload.get("sanitized") is not True:
        raise FinalizerContractError(f"{label} has invalid header")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != EXPECTED_SCENARIOS:
        raise FinalizerContractError(f"{label} must contain exactly the four scenarios")
    return scenarios


def recompute_scenario_validation(
    runtime_dir: Path,
    batch_id: str,
    name: str,
    scenario_summary: dict[str, Any],
    trace_manifest: dict[str, Any],
    log_manifest: dict[str, Any],
) -> ScenarioRuntimeValidation:
    definition = SCENARIO_BY_NAME[name]
    scenario_id = require_non_empty_string(f"{name}.scenario_id", scenario_summary.get("scenario_id"))
    agent_run_id = require_non_empty_string(f"{name}.agent_run_id", scenario_summary.get("agent_run_id"))
    summary_log_ids = tuple(require_id_list(name, "emitted_log_ids", scenario_summary.get("emitted_log_ids"), non_empty=False))
    scenario = RuntimeScenario(definition, batch_id, scenario_id, agent_run_id, summary_log_ids)
    trace_emission = parse_trace_emission(name, trace_manifest.get(name))
    log_emission = parse_log_emission(name, definition, log_manifest.get(name))
    if trace_emission.agent_run_id != agent_run_id:
        raise FinalizerContractError(f"{name} trace emission agent_run_id disagrees with summary")
    if tuple(log_emission.log_ids) != summary_log_ids:
        raise FinalizerContractError(f"{name} log emission IDs disagree with summary")

    retrieved_traces = load_retrieved_traces(runtime_dir, name, tuple(trace_emission.emitted_trace_ids))
    retrieved_logs = load_retrieved_logs(runtime_dir, name, tuple(log_emission.log_ids))
    verification = verify_preservation(scenario, trace_emission, retrieved_traces, log_emission, retrieved_logs)
    if not verification.passed:
        raise FinalizerContractError(f"{name} raw artifact preservation failed")
    stored_verification = load_json_artifact(runtime_dir / "verification" / f"{name}.json", f"{name} verification")
    if stored_verification != verification.to_dict():
        raise FinalizerContractError(f"{name} verification artifact contradicts recomputed preservation")

    bundle_payload = build_gate3_run_bundle(
        agent_run_id,
        retrieved_traces,
        retrieved_logs,
        {"gate": "3B", "scenario_name": name, "scenario_id": scenario_id, "batch_id": batch_id},
    )
    stored_bundle = load_json_artifact(runtime_dir / "run_bundles" / f"{name}.json", f"{name} run bundle")
    if stored_bundle != bundle_payload:
        raise FinalizerContractError(f"{name} run bundle artifact contradicts raw retrieved artifacts")
    bundle = load_run_bundle_payload(bundle_payload)
    evaluation = evaluate_run_bundle(bundle)
    actual_statuses = {item.rule_id: item.status.value for item in evaluation.rule_results}
    actual_verdict = evaluation.verdict.value
    if actual_statuses != definition.expected_rule_statuses:
        raise FinalizerContractError(f"{name} recomputed status map differs from immutable scenario definition")
    if actual_verdict != definition.expected_verdict:
        raise FinalizerContractError(f"{name} recomputed verdict differs from immutable scenario definition")
    stored_evaluation = load_json_artifact(runtime_dir / "evaluations" / f"{name}.json", f"{name} evaluation")
    stored_stable = dict(stored_evaluation)
    stored_stable.pop("evaluated_at", None)
    if stored_stable != evaluation.to_dict(include_evaluated_at=False):
        raise FinalizerContractError(f"{name} evaluation artifact contradicts recomputed evaluation")

    discovered_trace_ids = tuple(require_id_list(name, "discovered_trace_ids", scenario_summary.get("discovered_trace_ids"), non_empty=True))
    matched = (
        verification.passed
        and actual_statuses == definition.expected_rule_statuses
        and actual_verdict == definition.expected_verdict
        and set(discovered_trace_ids) == set(trace_emission.emitted_trace_ids)
    )
    return ScenarioRuntimeValidation(
        name=name,
        scenario_id=scenario_id,
        agent_run_id=agent_run_id,
        emitted_trace_ids=tuple(trace_emission.emitted_trace_ids),
        discovered_trace_ids=discovered_trace_ids,
        retrieved_trace_ids=tuple(trace.trace_id for trace in retrieved_traces),
        emitted_log_ids=tuple(log_emission.log_ids),
        retrieved_log_ids=tuple(log.log_id for log in retrieved_logs),
        trace_count=len(retrieved_traces),
        log_count=len(retrieved_logs),
        actual_rule_statuses=actual_statuses,
        actual_verdict=actual_verdict,
        trace_preservation_details=verification.trace_details.to_dict() if verification.trace_details else {},
        log_preservation_details=verification.log_details.to_dict() if verification.log_details else {},
        preservation_passed=verification.passed,
        matched_expectations=matched,
    )


def compare_scenario_summary(name: str, scenario: dict[str, Any], recomputed: ScenarioRuntimeValidation) -> None:
    definition = SCENARIO_BY_NAME[name]
    expected_statuses = require_status_map(name, "expected_rule_statuses", scenario.get("expected_rule_statuses"))
    if expected_statuses != definition.expected_rule_statuses:
        raise FinalizerContractError(f"{name} expected statuses differ from immutable scenario definition")
    if require_status_map(name, "actual_rule_statuses", scenario.get("actual_rule_statuses")) != recomputed.actual_rule_statuses:
        raise FinalizerContractError(f"{name} summary actual statuses contradict recomputed evaluation")
    expected_verdict = require_verdict(name, "expected_verdict", scenario.get("expected_verdict"))
    if expected_verdict != definition.expected_verdict:
        raise FinalizerContractError(f"{name} expected verdict differs from immutable scenario definition")
    if require_verdict(name, "actual_verdict", scenario.get("actual_verdict")) != recomputed.actual_verdict:
        raise FinalizerContractError(f"{name} summary actual verdict contradicts recomputed evaluation")
    if implied_verdict(expected_statuses) != expected_verdict or implied_verdict(recomputed.actual_rule_statuses) != recomputed.actual_verdict:
        raise FinalizerContractError(f"{name} verdict is inconsistent with status map")
    require_count(name, scenario, "trace", definition.expected_trace_count)
    require_count(name, scenario, "log", definition.expected_log_count)
    compare_set_field(name, scenario, "emitted_trace_ids", recomputed.emitted_trace_ids, non_empty=True)
    compare_set_field(name, scenario, "discovered_trace_ids", recomputed.discovered_trace_ids, non_empty=True)
    compare_set_field(name, scenario, "retrieved_trace_ids", recomputed.retrieved_trace_ids, non_empty=True)
    compare_set_field(name, scenario, "emitted_log_ids", recomputed.emitted_log_ids, non_empty=False)
    compare_set_field(name, scenario, "retrieved_log_ids", recomputed.retrieved_log_ids, non_empty=False)
    if scenario.get("actual_trace_count") != recomputed.trace_count or recomputed.trace_count != definition.expected_trace_count:
        raise FinalizerContractError(f"{name} trace count contradicts recomputed artifacts")
    if scenario.get("actual_log_count") != recomputed.log_count or recomputed.log_count != definition.expected_log_count:
        raise FinalizerContractError(f"{name} log count contradicts recomputed artifacts")
    if scenario.get("trace_preservation_details") != recomputed.trace_preservation_details:
        raise FinalizerContractError(f"{name} trace preservation details contradict recomputed preservation")
    if scenario.get("log_preservation_details") != recomputed.log_preservation_details:
        raise FinalizerContractError(f"{name} log preservation details contradict recomputed preservation")
    if scenario.get("trace_preservation_result") is not True or scenario.get("log_preservation_result") is not True:
        raise FinalizerContractError(f"{name} runner preservation booleans contradict recomputed preservation")
    if scenario.get("preservation_passed") is not recomputed.preservation_passed:
        raise FinalizerContractError(f"{name} runner preservation_passed contradicts recomputed preservation")
    if scenario.get("preservation_errors") not in ([], ()):
        raise FinalizerContractError(f"{name} summary preservation errors contradict recomputed preservation")
    for field, value in (
        ("matched_expectations", recomputed.matched_expectations),
        ("exact_status_match", True),
        ("verdict_match", True),
        ("evaluator_contract_match", True),
    ):
        if scenario.get(field) is not value:
            raise FinalizerContractError(f"{name} runner conclusion flag {field} contradicts recomputed result")


def compare_set_field(name: str, scenario: dict[str, Any], field: str, expected: tuple[str, ...], *, non_empty: bool) -> None:
    actual = require_id_list(name, field, scenario.get(field), non_empty=non_empty)
    if set(actual) != set(expected) or len(actual) != len(expected):
        raise FinalizerContractError(f"{name} {field} contradicts recomputed artifacts")


def parse_trace_emission(name: str, payload: Any) -> TraceEmissionResult:
    if not isinstance(payload, dict):
        raise FinalizerContractError(f"{name} trace emission must be an object")
    return TraceEmissionResult(
        require_matching_string(name, "trace emission scenario_name", payload.get("scenario_name")),
        require_non_empty_string(f"{name}.trace_emission.agent_run_id", payload.get("agent_run_id")),
        require_non_empty_string(f"{name}.trace_emission.service_name", payload.get("service_name")),
        tuple(require_id_list(name, "trace emission emitted_trace_ids", payload.get("emitted_trace_ids"), non_empty=True)),
        require_dict_of_strings(f"{name}.trace_emission.root_span_ids_by_trace_id", payload.get("root_span_ids_by_trace_id")),
        require_nested_string_map(f"{name}.trace_emission.span_ids_by_trace_id_and_name", payload.get("span_ids_by_trace_id_and_name"), allow_none=False),
        require_nested_string_map(f"{name}.trace_emission.parent_span_ids_by_trace_id_and_name", payload.get("parent_span_ids_by_trace_id_and_name"), allow_none=True),
        require_nested_object_map(f"{name}.trace_emission.expected_attributes_by_trace_id_and_name", payload.get("expected_attributes_by_trace_id_and_name")),
        require_non_empty_string(f"{name}.trace_emission.exported_at", payload.get("exported_at")),
        require_bool_true(f"{name}.trace_emission.exported", payload.get("exported", True)),
    )


def parse_log_emission(name: str, definition: Any, payload: Any) -> LogEmissionResult:
    if not isinstance(payload, dict):
        raise FinalizerContractError(f"{name} log emission must be an object")
    log_ids = tuple(require_id_list(name, "log emission log_ids", payload.get("log_ids"), non_empty=False))
    if len(log_ids) != definition.expected_log_count:
        raise FinalizerContractError(f"{name} log emission count disagrees with scenario definition")
    if payload.get("body_count") != len(log_ids):
        raise FinalizerContractError(f"{name} log emission body_count disagrees with log IDs")
    bodies = {log_id: f"{spec.body}: {name}:{spec.name}" for log_id, spec in zip(log_ids, definition.log_plan, strict=True)}
    return LogEmissionResult(
        require_matching_string(name, "log emission scenario_name", payload.get("scenario_name")),
        require_non_empty_string(f"{name}.log_emission.service_name", payload.get("service_name")),
        log_ids,
        require_dict_of_strings(f"{name}.log_emission.expected_agent_run_ids", payload.get("expected_agent_run_ids")),
        require_dict_of_strings(f"{name}.log_emission.expected_trace_ids", payload.get("expected_trace_ids")),
        require_dict_of_strings(f"{name}.log_emission.expected_span_ids", payload.get("expected_span_ids")),
        bodies,
        require_non_empty_string(f"{name}.log_emission.exported_at", payload.get("exported_at")),
        require_bool_true(f"{name}.log_emission.exported", payload.get("exported", True)),
    )


def load_retrieved_traces(runtime_dir: Path, name: str, expected_trace_ids: tuple[str, ...]) -> tuple[Trace, ...]:
    directory = runtime_dir / "retrieved_traces" / name
    actual_files = sorted(directory.glob("*.normalized.json"))
    expected_files = {f"{trace_id}.normalized.json" for trace_id in expected_trace_ids}
    if {path.name for path in actual_files} != expected_files:
        raise FinalizerContractError(f"{name} retrieved trace artifacts must exactly match emitted trace IDs")
    traces: list[Trace] = []
    for path in actual_files:
        payload = load_json_artifact(path, f"{name} retrieved trace {path.name}")
        trace = parse_retrieved_trace(name, payload)
        if path.name != f"{trace.trace_id}.normalized.json":
            raise FinalizerContractError(f"{name} retrieved trace filename disagrees with trace_id")
        traces.append(trace)
    ids = [trace.trace_id for trace in traces]
    if len(ids) != len(set(ids)):
        raise FinalizerContractError(f"{name} duplicate retrieved trace object exists")
    return tuple(traces)


def parse_retrieved_trace(name: str, payload: Any) -> Trace:
    if not isinstance(payload, dict):
        raise FinalizerContractError(f"{name} retrieved trace must be an object")
    trace_id = require_non_empty_string(f"{name}.retrieved_trace.trace_id", payload.get("trace_id"))
    spans_payload = payload.get("spans")
    if not isinstance(spans_payload, list):
        raise FinalizerContractError(f"{name} retrieved trace spans must be a list")
    spans: list[Span] = []
    for index, item in enumerate(spans_payload):
        if not isinstance(item, dict):
            raise FinalizerContractError(f"{name} retrieved trace span {index} must be an object")
        spans.append(
            Span(
                require_non_empty_string(f"{name}.span[{index}].trace_id", item.get("trace_id")),
                require_non_empty_string(f"{name}.span[{index}].span_id", item.get("span_id")),
                require_optional_string(f"{name}.span[{index}].parent_span_id", item.get("parent_span_id")),
                require_non_empty_string(f"{name}.span[{index}].span_name", item.get("span_name")),
                parse_iso_datetime(f"{name}.span[{index}].start_time", item.get("start_time")),
                parse_iso_datetime(f"{name}.span[{index}].end_time", item.get("end_time")),
                require_int(f"{name}.span[{index}].duration_nano", item.get("duration_nano")),
                require_dict(f"{name}.span[{index}].status", item.get("status")),
                require_dict(f"{name}.span[{index}].attributes", item.get("attributes")),
                require_dict(f"{name}.span[{index}].resource_attributes", item.get("resource_attributes")),
                require_optional_string(f"{name}.span[{index}].service_name", item.get("service_name")),
            )
        )
    source = payload.get("source")
    if source != Source.TRACE_API.value:
        raise FinalizerContractError(f"{name} retrieved trace source must be {Source.TRACE_API.value}")
    return Trace(
        trace_id=trace_id,
        spans=spans,
        retrieved_at=parse_iso_datetime(f"{name}.retrieved_trace.retrieved_at", payload.get("retrieved_at")),
        source=Source.TRACE_API,
        raw_artifact=require_optional_string(f"{name}.retrieved_trace.raw_artifact", payload.get("raw_artifact")),
        metadata=require_dict(f"{name}.retrieved_trace.metadata", payload.get("metadata", {})),
    )


def load_retrieved_logs(runtime_dir: Path, name: str, expected_log_ids: tuple[str, ...]) -> tuple[RetrievedLog, ...]:
    payload = load_json_artifact(runtime_dir / "retrieved_logs" / f"{name}.normalized.json", f"{name} retrieved logs")
    if not isinstance(payload, dict) or payload.get("sanitized") is not True:
        raise FinalizerContractError(f"{name} retrieved logs artifact must be a sanitized object")
    logs_payload = payload.get("logs")
    if not isinstance(logs_payload, list):
        raise FinalizerContractError(f"{name} retrieved logs must contain a logs list")
    logs = tuple(parse_retrieved_log(name, index, item) for index, item in enumerate(logs_payload))
    ids = [log.log_id for log in logs]
    if len(ids) != len(set(ids)):
        raise FinalizerContractError(f"{name} duplicate retrieved log ID exists")
    if set(ids) != set(expected_log_ids) or len(ids) != len(expected_log_ids):
        raise FinalizerContractError(f"{name} retrieved log IDs must exactly match emitted log IDs")
    return logs


def parse_retrieved_log(name: str, index: int, payload: Any) -> RetrievedLog:
    if not isinstance(payload, dict):
        raise FinalizerContractError(f"{name} retrieved log {index} must be an object")
    return RetrievedLog(
        require_non_empty_string(f"{name}.log[{index}].log_id", payload.get("log_id")),
        require_optional_string(f"{name}.log[{index}].timestamp", payload.get("timestamp")),
        require_optional_string(f"{name}.log[{index}].trace_id", payload.get("trace_id")),
        require_optional_string(f"{name}.log[{index}].span_id", payload.get("span_id")),
        payload.get("body"),
        require_dict(f"{name}.log[{index}].attributes", payload.get("attributes")),
        require_dict(f"{name}.log[{index}].resource_attributes", payload.get("resource_attributes")),
        require_optional_string(f"{name}.log[{index}].service_name", payload.get("service_name")),
        require_non_empty_string(f"{name}.log[{index}].source", payload.get("source")),
    )


def require_scenario_contract(name: str, scenario: dict[str, Any]) -> None:
    definition = SCENARIO_BY_NAME[name]
    expected_statuses = require_status_map(name, "expected_rule_statuses", scenario.get("expected_rule_statuses"))
    actual_statuses = require_status_map(name, "actual_rule_statuses", scenario.get("actual_rule_statuses"))
    if expected_statuses != definition.expected_rule_statuses:
        raise FinalizerContractError(f"{name} expected statuses differ from immutable scenario definition")
    if expected_statuses != actual_statuses:
        raise FinalizerContractError(f"{name} expected and actual status maps differ")
    expected_verdict = require_verdict(name, "expected_verdict", scenario.get("expected_verdict"))
    actual_verdict = require_verdict(name, "actual_verdict", scenario.get("actual_verdict"))
    if expected_verdict != definition.expected_verdict:
        raise FinalizerContractError(f"{name} expected verdict differs from immutable scenario definition")
    if expected_verdict != actual_verdict:
        raise FinalizerContractError(f"{name} expected and actual verdicts differ")
    if implied_verdict(expected_statuses) != expected_verdict or implied_verdict(actual_statuses) != actual_verdict:
        raise FinalizerContractError(f"{name} verdict is inconsistent with status map")
    require_count(name, scenario, "trace", definition.expected_trace_count)
    require_count(name, scenario, "log", definition.expected_log_count)
    require_trace_ids(name, scenario, definition.expected_trace_count)
    require_log_ids(name, scenario, definition.expected_log_count)
    require_preservation_details(name, scenario)


def require_status_map(name: str, field: str, value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise FinalizerContractError(f"{name} {field} must be a dictionary")
    if set(value) != EXPECTED_RULE_IDS or len(value) != 14:
        raise FinalizerContractError(f"{name} {field} must contain the exact canonical rule IDs")
    statuses: dict[str, str] = {}
    valid = {item.value for item in RuleStatus}
    for rule_id, status in value.items():
        if status not in valid:
            raise FinalizerContractError(f"{name} {field} contains invalid rule status")
        statuses[str(rule_id)] = str(status)
    return statuses


def require_verdict(name: str, field: str, value: object) -> str:
    valid = {item.value for item in Verdict}
    if value not in valid:
        raise FinalizerContractError(f"{name} {field} must be a valid verdict")
    return str(value)


def implied_verdict(statuses: dict[str, str]) -> str:
    results = [RULE_BY_ID[rule_id].result(RuleStatus(status), "", observed={}) for rule_id, status in statuses.items()]
    return verdict_from_rule_results(results).value


def require_count(name: str, scenario: dict[str, Any], kind: str, definition_count: int) -> None:
    expected = scenario.get(f"expected_{kind}_count")
    actual = scenario.get(f"actual_{kind}_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or not isinstance(actual, int) or isinstance(actual, bool):
        raise FinalizerContractError(f"{name} {kind} counts must be real integers")
    if expected != actual or expected != definition_count:
        raise FinalizerContractError(f"{name} {kind} counts disagree")


def require_trace_ids(name: str, scenario: dict[str, Any], expected_count: int) -> None:
    emitted = require_id_list(name, "emitted_trace_ids", scenario.get("emitted_trace_ids"), non_empty=True)
    discovered = require_id_list(name, "discovered_trace_ids", scenario.get("discovered_trace_ids"), non_empty=True)
    retrieved = require_id_list(name, "retrieved_trace_ids", scenario.get("retrieved_trace_ids"), non_empty=True)
    if set(emitted) != set(discovered) or set(emitted) != set(retrieved):
        raise FinalizerContractError(f"{name} trace ID sets disagree")
    required_count = 2 if name == "block_fragmented_run" else 1
    if len(set(emitted)) != expected_count or len(set(emitted)) != required_count:
        raise FinalizerContractError(f"{name} trace ID count is invalid")


def require_log_ids(name: str, scenario: dict[str, Any], expected_count: int) -> None:
    emitted = require_id_list(name, "emitted_log_ids", scenario.get("emitted_log_ids"), non_empty=False)
    retrieved = require_id_list(name, "retrieved_log_ids", scenario.get("retrieved_log_ids"), non_empty=False)
    if expected_count == 0:
        if emitted != [] or retrieved != []:
            raise FinalizerContractError(f"{name} zero-log scenario must have empty log IDs")
        return
    if set(emitted) != set(retrieved) or len(set(emitted)) != expected_count:
        raise FinalizerContractError(f"{name} log ID sets disagree")


def require_id_list(name: str, field: str, value: object, *, non_empty: bool) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value) or not all(isinstance(item, str) and item for item in value):
        raise FinalizerContractError(f"{name} {field} must be a {'non-empty ' if non_empty else ''}list of strings")
    if len(value) != len(set(value)):
        raise FinalizerContractError(f"{name} {field} contains duplicate IDs")
    return list(value)


def require_non_empty_string(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FinalizerContractError(f"{field} must be a non-empty string")
    return value


def require_matching_string(expected: str, field: str, value: object) -> str:
    actual = require_non_empty_string(field, value)
    if actual != expected:
        raise FinalizerContractError(f"{field} must be {expected}")
    return actual


def require_optional_string(field: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FinalizerContractError(f"{field} must be a string or null")
    return value


def require_bool_true(field: str, value: object) -> bool:
    if value is not True:
        raise FinalizerContractError(f"{field} must be true")
    return True


def require_int(field: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FinalizerContractError(f"{field} must be an integer")
    return value


def require_dict(field: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FinalizerContractError(f"{field} must be an object")
    return dict(value)


def require_dict_of_strings(field: str, value: object) -> dict[str, str]:
    data = require_dict(field, value)
    if not all(isinstance(key, str) and isinstance(item, str) and item for key, item in data.items()):
        raise FinalizerContractError(f"{field} must be a string-to-string object")
    return {str(key): str(item) for key, item in data.items()}


def require_nested_string_map(field: str, value: object, *, allow_none: bool) -> dict[str, dict[str, str | None]]:
    data = require_dict(field, value)
    result: dict[str, dict[str, str | None]] = {}
    for outer_key, nested in data.items():
        if not isinstance(outer_key, str) or not isinstance(nested, dict):
            raise FinalizerContractError(f"{field} must be a nested object")
        result[outer_key] = {}
        for inner_key, item in nested.items():
            if not isinstance(inner_key, str):
                raise FinalizerContractError(f"{field} keys must be strings")
            if item is None and allow_none:
                result[outer_key][inner_key] = None
            elif isinstance(item, str) and item:
                result[outer_key][inner_key] = item
            else:
                raise FinalizerContractError(f"{field} values must be non-empty strings{' or null' if allow_none else ''}")
    return result


def require_nested_object_map(field: str, value: object) -> dict[str, dict[str, dict[str, object]]]:
    data = require_dict(field, value)
    result: dict[str, dict[str, dict[str, object]]] = {}
    for outer_key, nested in data.items():
        if not isinstance(outer_key, str) or not isinstance(nested, dict):
            raise FinalizerContractError(f"{field} must be a nested object")
        result[outer_key] = {}
        for inner_key, item in nested.items():
            if not isinstance(inner_key, str) or not isinstance(item, dict):
                raise FinalizerContractError(f"{field} leaves must be objects")
            result[outer_key][inner_key] = dict(item)
    return result


def parse_iso_datetime(field: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise FinalizerContractError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FinalizerContractError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise FinalizerContractError(f"{field} must include timezone information")
    return parsed


def require_preservation_details(name: str, scenario: dict[str, Any]) -> None:
    if scenario.get("preservation_passed") is not True:
        raise FinalizerContractError(f"{name} preservation_passed must be true")
    if scenario.get("preservation_errors") not in ([], ()):
        raise FinalizerContractError(f"{name} preservation errors must be empty")
    for field in ("trace_preservation_details", "log_preservation_details"):
        details = scenario.get(field)
        if not isinstance(details, dict) or details.get("passed") is not True:
            raise FinalizerContractError(f"{name} {field}.passed must be true")
        for key, value in details.items():
            if key in {"errors", "passed"}:
                continue
            if value is not True:
                raise FinalizerContractError(f"{name} {field}.{key} must be true")
        if details.get("errors") not in ([], ()):
            raise FinalizerContractError(f"{name} {field}.errors must be empty")


def validate_environment(env: dict[str, Any]) -> None:
    for key in ("health_ok", "authenticated_trace_api_access", "authenticated_log_api_access"):
        if env.get(key) is not True:
            raise FinalizerContractError(f"environment_check.{key} must be true")
    for key in ("signoz_version", "trace_otlp_endpoint", "log_otlp_endpoint", "checked_at"):
        if not isinstance(env.get(key), str) or not str(env.get(key)).strip():
            raise FinalizerContractError(f"environment_check.{key} must be non-empty")
    validate_endpoint("trace_otlp_endpoint", str(env["trace_otlp_endpoint"]), "/v1/traces")
    validate_endpoint("log_otlp_endpoint", str(env["log_otlp_endpoint"]), "/v1/logs")


def validate_endpoint(name: str, value: str, suffix: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path.endswith(suffix):
        raise FinalizerContractError(f"environment_check.{name} must be an HTTP(S) endpoint ending in {suffix}")


def run_verification_commands() -> list[VerificationCommandResult]:
    results: list[VerificationCommandResult] = []
    for name, args in VERIFICATION_COMMANDS:
        command = [sys.executable, *args]
        completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, shell=False, timeout=180)
        stdout = sanitize_text(completed.stdout)
        stderr = sanitize_text(completed.stderr)
        results.append(
            VerificationCommandResult(
                name=name,
                command=display_command(args),
                exit_code=completed.returncode,
                passed=completed.returncode == 0,
                test_count=parse_test_count(stdout + "\n" + stderr),
                stdout_summary=summarize_output(stdout),
                stderr_summary=summarize_output(stderr),
                captured_at=now_iso(),
            )
        )
    return results


def display_command(args: list[str]) -> list[str]:
    return ["<current-python>", *args]


def parse_test_count(output: str) -> int | None:
    matches = list(PYTEST_COUNT_RE.finditer(output))
    return int(matches[-1].group("count")) if matches else None


def summarize_output(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    interesting = [line for line in lines if " passed" in line or " failed" in line or "ERROR" in line or "No broken requirements found" in line or "Listing '" in line or "fixtures" in line.lower() or "evaluat" in line.lower()]
    selected = interesting[-8:] if interesting else lines[-8:]
    return "\n".join(selected)[:2000]


def validate_verification_results(results: list[VerificationCommandResult]) -> None:
    names = [item.name for item in results]
    duplicate_names = {name for name in names if names.count(name) > 1}
    if duplicate_names:
        raise FinalizerContractError("verification command results contain duplicate names")
    actual = set(names)
    if actual != REQUIRED_VERIFICATION_NAMES:
        missing = sorted(REQUIRED_VERIFICATION_NAMES - actual)
        unknown = sorted(actual - REQUIRED_VERIFICATION_NAMES)
        raise FinalizerContractError(f"verification command result set mismatch; missing={missing}, unknown={unknown}")
    for item in results:
        if item.command != EXPECTED_VERIFICATION_COMMANDS[item.name]:
            raise FinalizerContractError(f"{item.name} command must exactly match finalizer inventory")
        if item.exit_code != 0:
            raise FinalizerContractError(f"{item.name} exit_code must be 0")
        if item.passed is not True:
            raise FinalizerContractError(f"{item.name} passed must be true")
        parse_iso_datetime(f"{item.name}.captured_at", item.captured_at)
        if item.name in PYTEST_VERIFICATION_NAMES:
            if not isinstance(item.test_count, int) or isinstance(item.test_count, bool) or item.test_count <= 0:
                raise FinalizerContractError(f"{item.name} pytest test_count must be positive")
        elif item.test_count is not None:
            raise FinalizerContractError(f"{item.name} non-pytest test_count must be null")


SAFE_PLACEHOLDER_VALUES = {
    "<redacted>",
    "<set>",
    "<your-api-key>",
    "<your-service-account-key>",
    "example-key",
    "fake-key",
    "fake-dotenv-secret",
    "fake-token",
    "synthetic-token",
    "test-api-key",
    "changeme-for-local-testing",
}


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("signoz_api_key", re.compile(r"\bSIGNOZ_API_KEY\s*=\s*['\"]?([^'\"\s#]+)")),
    ("authorization_header", re.compile(r"(?i)\bAuthorization\s*:\s*(?:Bearer\s+)?([^'\"\s#]+)")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{16,})")),
    ("private_key", re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
    ("cookie", re.compile(r"(?i)\bCookie\s*:\s*([^\\n]+)")),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*=\s*['\"]([^'\"]{8,})['\"]")),
    ("service_account", re.compile(r"(?i)\"(?:client_email|private_key|private_key_id)\"\s*:\s*\"([^\"]+)\"")),
)


def run_secret_scan() -> dict[str, Any]:
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, shell=False, timeout=30)
    if tracked.returncode != 0:
        raise FinalizerUsageError("git ls-files failed during secret scan")
    files = [line for line in tracked.stdout.splitlines() if line.strip()]
    findings: list[dict[str, Any]] = []
    for rel in files:
        path = REPO_ROOT / rel
        if path.suffix.lower() in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text_payload_for_secrets(rel.replace("\\", "/"), text))
    return {
        "tracked_files_scanned": len(files),
        "tracked_findings_count": len(findings),
        "scanned_tracked_file_count": len(files),
        "findings_count": len(findings),
        "findings": findings,
        "passed": not findings,
        "sanitized": True,
    }


def scan_text_payload_for_secrets(payload_name: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    normalized_path = payload_name.replace("\\", "/")
    for line_number, line in enumerate(text.splitlines(), 1):
        for category, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                candidate = normalize_candidate(match.group(1) if match.groups() else match.group(0))
                if category != "private_key" and candidate in SAFE_PLACEHOLDER_VALUES:
                    continue
                if is_scanner_source_declaration(normalized_path, line, category):
                    continue
                findings.append({"path": normalized_path, "line": line_number, "category": category})
    return findings


def normalize_candidate(value: str) -> str:
    return value.strip().strip("'\"").strip()


def is_scanner_source_declaration(path: str, line: str, category: str) -> bool:
    return (
        path == "gate3b/finalize_evidence.py"
        and category in {name for name, _pattern in SECRET_PATTERNS}
        and ("re.compile(" in line or "re.sub(" in line or "SECRET_PATTERNS" in line)
    )


def scan_proposed_evidence(evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for name, payload in evidence.items():
        text = serialize_json(payload)
        findings.extend(scan_text_payload_for_secrets(name, text))
    return {
        "proposed_evidence_files_scanned": len(evidence),
        "proposed_evidence_findings_count": len(findings),
        "findings": findings,
        "passed": not findings,
        "sanitized": True,
    }


def serialize_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"


def serialize_evidence(evidence: dict[str, dict[str, Any]]) -> SerializedEvidenceSet:
    serialized_text = {name: serialize_json(payload) for name, payload in evidence.items()}
    findings: list[dict[str, Any]] = []
    for name, text in serialized_text.items():
        findings.extend(scan_text_payload_for_secrets(name, text))
    scan_result = {
        "proposed_evidence_files_scanned": len(serialized_text),
        "proposed_evidence_findings_count": len(findings),
        "findings": findings,
        "passed": not findings,
        "sanitized": True,
    }
    return SerializedEvidenceSet(evidence, serialized_text, tuple(sorted(serialized_text)), scan_result)


def merge_secret_scans(tracked: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    return {
        "tracked_files_scanned": tracked.get("tracked_files_scanned", tracked.get("scanned_tracked_file_count")),
        "tracked_findings_count": tracked.get("tracked_findings_count", tracked.get("findings_count")),
        "proposed_evidence_files_scanned": proposed["proposed_evidence_files_scanned"],
        "proposed_evidence_findings_count": proposed["proposed_evidence_findings_count"],
        "passed": bool(tracked.get("passed")) and bool(proposed.get("passed")),
        "sanitized": True,
    }


def build_evidence(
    summary: dict[str, Any],
    runtime_validation: RuntimeBatchValidation,
    command_results: list[VerificationCommandResult],
    secret_scan: dict[str, Any],
    completion: ValidatedGate3BCompletion,
) -> dict[str, dict[str, Any]]:
    batch_id = str(summary["batch_id"])
    env = runtime_validation.environment_check
    compat = compatibility_contract()
    log_contract = log_api_contract(str(env.get("signoz_version") or "unknown")) | {
        "opentelemetry_import_contract": compat,
        "opentelemetry_api_import_path_used": compat.get("logger_provider_path"),
        "opentelemetry_otlp_exporter_import_path_used": compat.get("otlp_exporter_path") or compat.get("exporter_path"),
        "opentelemetry_package_versions": compat.get("opentelemetry_versions"),
        "private_fallback_used": compat.get("private_fallback_used"),
        "gate2_public_query_method": "SigNozAPIClient.query_range",
        "sanitized": True,
    }
    verification = {
        "captured_at": now_iso(),
        "commands": [item.to_dict() for item in command_results],
        "all_passed": all(item.passed for item in command_results),
        "sanitized": True,
    }
    live = {
        "batch_id": batch_id,
        "captured_at": summary.get("captured_at"),
        "signoz_version": env.get("signoz_version"),
        "environment_check": env,
        "environment_evidence_source": "runner_observed_and_finalizer_cross_checked",
        "environment_live_checks_repeated_by_finalizer": False,
        "trace_otlp_endpoint": env.get("trace_otlp_endpoint"),
        "log_otlp_endpoint": env.get("log_otlp_endpoint"),
        "authoritative_sources": {"trace": AUTHORITATIVE_TRACE_SOURCE, "log": AUTHORITATIVE_LOG_SOURCE},
        "scenario_results": summary.get("scenarios"),
        "recomputed_scenario_validation": runtime_validation.to_summary_dict()["scenario_validations"],
        "scenario_count": summary.get("scenario_count"),
        "completed_count": summary.get("completed_count"),
        "matched_count": summary.get("matched_count"),
        "failed_count": summary.get("failed_count"),
        "all_expectations_matched": summary.get("all_expectations_matched"),
        "live_exit_code": summary.get("live_exit_code"),
        "sanitized": True,
    }
    decision = {
        "gate": "3B",
        "gate3b_complete": completion.gate3b_complete,
        "evidence_batch_id": batch_id,
        "ruleset_version": GATE3B_RULESET_VERSION,
        "authoritative_trace_source": AUTHORITATIVE_TRACE_SOURCE,
        "authoritative_log_source": AUTHORITATIVE_LOG_SOURCE,
        "trace_export_verified": completion.all_trace_ids_matched,
        "log_export_verified": completion.all_log_ids_matched,
        "trace_retrieval_verified": completion.all_trace_ids_matched,
        "log_retrieval_verified": completion.all_log_ids_matched,
        "full_preservation_verified": completion.full_preservation_verified,
        "run_bundle_validation_verified": completion.all_status_maps_matched and completion.all_verdicts_matched,
        "tg_tel_003b_verified": completion.all_scenarios_matched,
        "tg_tel_008_verified": completion.all_scenarios_matched,
        "exact_status_maps_verified": completion.all_status_maps_matched,
        "all_scenarios_matched": completion.all_scenarios_matched,
        "all_tests_passed": completion.all_tests_passed,
        "secret_scan_passed": completion.tracked_secret_scan_passed,
        "tracked_secret_scan_passed": completion.tracked_secret_scan_passed,
        "proposed_evidence_scan_passed": completion.proposed_evidence_scan_passed,
        "exact_verification_command_set": completion.exact_verification_command_set,
        "scenario_validation_recomputed_by_finalizer": True,
        "runner_scenario_conclusion_flags_trusted_as_authoritative": False,
        "environment_evidence_source": "runner_observed_and_finalizer_cross_checked",
        "environment_live_checks_repeated_by_finalizer": False,
        "trace_preservation_recomputed_from_raw_artifacts": True,
        "log_preservation_recomputed_from_raw_artifacts": True,
        "evaluation_recomputed_from_raw_artifacts": True,
        "exact_scanned_bytes_written": True,
        "atomic_evidence_publication_succeeded": True,
        "opentelemetry_import_paths_reported_public_private": True,
        "live_exit_code": completion.live_exit_code,
        "finalizer_exit_code": completion.finalizer_exit_code,
        "next_action": "Begin TG-AGT v1 agent-behaviour rules using controlled agent execution scenarios, while requiring both traceguard-telemetry-v2 and the finalized Gate 3B evidence as mandatory preconditions.",
        "sanitized": True,
    }
    return {
        "gate3b_scenario_catalog.json": scenario_catalogue(),
        "gate3b_log_api_contract.json": log_contract,
        "gate3b_live_results.json": live,
        "gate3b_verification_results.json": verification,
        "gate3b_secret_scan.json": {key: value for key, value in secret_scan.items() if key != "findings"} | {"sanitized": True},
        "gate3b_decision.json": decision,
    }


def contains_placeholder(evidence: dict[str, Any]) -> bool:
    return bool(PLACEHOLDER_RE.search(json.dumps(evidence, sort_keys=True, default=str)))


def write_evidence(evidence: dict[str, dict[str, Any]]) -> None:
    write_serialized_evidence(serialize_evidence(evidence))


def write_serialized_evidence(serialized: SerializedEvidenceSet) -> None:
    if not serialized.scan_result["passed"]:
        raise FinalizerContractError("refusing to write unclean serialized evidence")
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    temp_paths: list[Path] = []
    backup_paths: list[tuple[Path, Path]] = []
    try:
        for name in serialized.filenames:
            target = EVIDENCE_ROOT / name
            temp = EVIDENCE_ROOT / f".tmp-{os.getpid()}-{name}"
            with temp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(serialized.serialized_text[name])
                handle.flush()
                os.fsync(handle.fileno())
            temp_paths.append(temp)
        for name in serialized.filenames:
            target = EVIDENCE_ROOT / name
            if target.exists():
                backup = EVIDENCE_ROOT / f".bak-{os.getpid()}-{name}"
                target.replace(backup)
                backup_paths.append((backup, target))
        try:
            for name in serialized.filenames:
                (EVIDENCE_ROOT / f".tmp-{os.getpid()}-{name}").replace(EVIDENCE_ROOT / name)
        except Exception:
            for backup, target in reversed(backup_paths):
                if backup.exists():
                    backup.replace(target)
            raise
        for backup, _target in backup_paths:
            backup.unlink(missing_ok=True)
    except Exception:
        for temp in temp_paths:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        for backup, target in reversed(backup_paths):
            try:
                if backup.exists() and not target.exists():
                    backup.replace(target)
            except OSError:
                pass
        raise


def sanitize_text(text: str) -> str:
    sanitized = re.sub(r"(?i)(SIGNOZ_API_KEY\s*=\s*)[^\s'\"#]+", r"\1<redacted>", text)
    sanitized = re.sub(r"(?i)(SIGNOZ-API-KEY[:=]\s*)[^\s'\"#]+", r"\1<redacted>", sanitized)
    sanitized = re.sub(r"(?i)(Authorization:\s*)(Bearer\s+)?[^\s'\"#]+", r"\1<redacted>", sanitized)
    sanitized = re.sub(r"(?i)(Cookie:\s*)[^\n]+", r"\1<redacted>", sanitized)
    sanitized = re.sub(r"(?i)(password\s*=\s*)['\"][^'\"]+['\"]", r"\1'<redacted>'", sanitized)
    return sanitized


if __name__ == "__main__":
    raise SystemExit(main())
