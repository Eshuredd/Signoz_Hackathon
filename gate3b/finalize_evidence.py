from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate3.models import RULESET_VERSION, RuleStatus, Verdict, verdict_from_rule_results
from gate3.rules import RULE_BY_ID
from gate3b.log_api_adapter import log_api_contract
from gate3b.models import AUTHORITATIVE_LOG_SOURCE, AUTHORITATIVE_TRACE_SOURCE, RULESET_VERSION as GATE3B_RULESET_VERSION, now_iso
from gate3b.otel_log_compat import compatibility_contract
from gate3b.runner import write_json
from gate3b.scenarios import SCENARIO_DEFINITIONS, scenario_catalogue


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
            scenario_count=runtime_validation["recomputed_completed_count"],
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
        evidence = build_evidence(summary, runtime_validation["environment_check"], command_results, secret_scan, completion)
        if contains_placeholder(evidence):
            print(json.dumps({"error": "placeholder_evidence_detected", "sanitized": True}, indent=2, sort_keys=True))
            return 4
        proposed_scan = scan_proposed_evidence(evidence)
        secret_scan = merge_secret_scans(secret_scan, proposed_scan)
        completion = ValidatedGate3BCompletion(**(completion.__dict__ | {"proposed_evidence_scan_passed": proposed_scan["passed"]}))
        evidence = build_evidence(summary, runtime_validation["environment_check"], command_results, secret_scan, completion)
        if not proposed_scan["passed"]:
            print(json.dumps({"error": "proposed_evidence_secret_scan_failed", "findings": proposed_scan["findings"], "sanitized": True}, indent=2, sort_keys=True))
            return 3
        if args.dry_run:
            print(json.dumps({"dry_run": True, "would_write": sorted(evidence), "evidence": evidence, "sanitized": True}, indent=2, sort_keys=True, default=str))
            return 0
        write_evidence(evidence)
        print(json.dumps({"finalized": True, "batch_id": args.batch_id, "files": sorted(evidence), "sanitized": True}, indent=2, sort_keys=True))
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


def validate_completion_contract(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("live_exit_code") != 0:
        raise FinalizerContractError("live_exit_code must be 0")
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != EXPECTED_SCENARIOS:
        raise FinalizerContractError("summary must contain the four exact Gate 3B scenarios")
    recomputed_completed_count = 0
    recomputed_matched_count = 0
    for name, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            raise FinalizerContractError(f"{name} scenario summary must be an object")
        require_scenario_contract(name, scenario)
        recomputed_completed_count += 1
        recomputed_matched_count += 1
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
    env = summary.get("environment_check")
    if not isinstance(env, dict):
        raise FinalizerContractError("environment_check is required")
    validate_environment(env)
    if GATE3B_RULESET_VERSION != "traceguard-telemetry-v2" or RULESET_VERSION != GATE3B_RULESET_VERSION:
        raise FinalizerContractError("Gate 3B ruleset version mismatch")
    return {
        "environment_check": env,
        "recomputed_completed_count": recomputed_completed_count,
        "recomputed_matched_count": recomputed_matched_count,
        "recomputed_failed_count": recomputed_failed_count,
        "recomputed_all_expectations_matched": recomputed_all_expectations_matched,
    }


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
        if item.exit_code != 0:
            raise FinalizerContractError(f"{item.name} exit_code must be 0")
        if item.passed is not True:
            raise FinalizerContractError(f"{item.name} passed must be true")
        if not item.command:
            raise FinalizerContractError(f"{item.name} command must be non-empty")
        if not item.captured_at:
            raise FinalizerContractError(f"{item.name} captured_at must be non-empty")


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
    for line_number, line in enumerate(text.splitlines(), 1):
        for category, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                candidate = normalize_candidate(match.group(1) if match.groups() else match.group(0))
                if category != "private_key" and (candidate in SAFE_PLACEHOLDER_VALUES or looks_like_regex_source(candidate)):
                    continue
                findings.append({"path": payload_name.replace("\\", "/"), "line": line_number, "category": category})
    return findings


def normalize_candidate(value: str) -> str:
    return value.strip().strip("'\"").strip()


def looks_like_regex_source(value: str) -> bool:
    return "\\" in value or "(" in value or ")" in value or "[" in value or "]" in value


def scan_proposed_evidence(evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for name, payload in evidence.items():
        text = json.dumps(payload, indent=2, sort_keys=True, default=str)
        findings.extend(scan_text_payload_for_secrets(name, text))
    return {
        "proposed_evidence_files_scanned": len(evidence),
        "proposed_evidence_findings_count": len(findings),
        "findings": findings,
        "passed": not findings,
        "sanitized": True,
    }


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
    env: dict[str, Any],
    command_results: list[VerificationCommandResult],
    secret_scan: dict[str, Any],
    completion: ValidatedGate3BCompletion,
) -> dict[str, dict[str, Any]]:
    batch_id = str(summary["batch_id"])
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
        "trace_otlp_endpoint": env.get("trace_otlp_endpoint"),
        "log_otlp_endpoint": env.get("log_otlp_endpoint"),
        "authoritative_sources": {"trace": AUTHORITATIVE_TRACE_SOURCE, "log": AUTHORITATIVE_LOG_SOURCE},
        "scenario_results": summary.get("scenarios"),
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
        "validation_recomputed_by_finalizer": True,
        "runner_flags_trusted_as_authoritative": False,
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
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    for name, payload in evidence.items():
        write_json(EVIDENCE_ROOT / name, payload)


def sanitize_text(text: str) -> str:
    sanitized = re.sub(r"(?i)(SIGNOZ_API_KEY\s*=\s*)[^\s'\"#]+", r"\1<redacted>", text)
    sanitized = re.sub(r"(?i)(SIGNOZ-API-KEY[:=]\s*)[^\s'\"#]+", r"\1<redacted>", sanitized)
    sanitized = re.sub(r"(?i)(Authorization:\s*)(Bearer\s+)?[^\s'\"#]+", r"\1<redacted>", sanitized)
    sanitized = re.sub(r"(?i)(Cookie:\s*)[^\n]+", r"\1<redacted>", sanitized)
    sanitized = re.sub(r"(?i)(password\s*=\s*)['\"][^'\"]+['\"]", r"\1'<redacted>'", sanitized)
    return sanitized


if __name__ == "__main__":
    raise SystemExit(main())
