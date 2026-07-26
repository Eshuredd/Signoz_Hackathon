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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate3.models import RULESET_VERSION
from gate3b.log_api_adapter import log_api_contract
from gate3b.models import AUTHORITATIVE_LOG_SOURCE, AUTHORITATIVE_TRACE_SOURCE, RULESET_VERSION as GATE3B_RULESET_VERSION, now_iso
from gate3b.otel_log_compat import compatibility_contract
from gate3b.runner import write_json
from gate3b.scenarios import SCENARIO_DEFINITIONS, scenario_catalogue


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / ".traceguard" / "runtime" / "gate3b"
EVIDENCE_ROOT = REPO_ROOT / "gate3b" / "evidence"
EXPECTED_SCENARIOS = {item.name for item in SCENARIO_DEFINITIONS}
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize sanitized committed Gate 3B evidence from a complete runtime batch.")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        summary = load_summary(args.batch_id)
        env = validate_completion_contract(summary)
        command_results = run_verification_commands()
        failed = [item for item in command_results if not item.passed]
        if failed:
            print(json.dumps({"error": "verification_command_failed", "failed_command": failed[0].name, "sanitized": True}, indent=2, sort_keys=True))
            return 3
        secret_scan = run_secret_scan()
        if not secret_scan["passed"]:
            print(json.dumps({"error": "tracked_secret_scan_failed", "findings": secret_scan["findings"], "sanitized": True}, indent=2, sort_keys=True))
            return 3
        evidence = build_evidence(summary, env, command_results, secret_scan)
        if contains_placeholder(evidence):
            print(json.dumps({"error": "placeholder_evidence_detected", "sanitized": True}, indent=2, sort_keys=True))
            return 4
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
    required = {
        "scenario_count": 4,
        "completed_count": 4,
        "matched_count": 4,
        "failed_count": 0,
        "all_expectations_matched": True,
        "live_exit_code": 0,
    }
    for key, value in required.items():
        if summary.get(key) != value:
            raise FinalizerContractError(f"{key} must be {value!r}")
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != EXPECTED_SCENARIOS:
        raise FinalizerContractError("summary must contain the four exact Gate 3B scenarios")
    for name, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            raise FinalizerContractError(f"{name} scenario summary must be an object")
        require_scenario_contract(name, scenario)
    env = summary.get("environment_check")
    if not isinstance(env, dict):
        raise FinalizerContractError("environment_check is required")
    if GATE3B_RULESET_VERSION != "traceguard-telemetry-v2" or RULESET_VERSION != GATE3B_RULESET_VERSION:
        raise FinalizerContractError("Gate 3B ruleset version mismatch")
    return env


def require_scenario_contract(name: str, scenario: dict[str, Any]) -> None:
    required_truthy = ("emitted_trace_ids", "retrieved_trace_ids", "expected_verdict", "actual_verdict", "expected_rule_statuses", "actual_rule_statuses")
    for key in required_truthy:
        if not scenario.get(key):
            raise FinalizerContractError(f"{name} missing {key}")
    if len(scenario["expected_rule_statuses"]) != 14 or len(scenario["actual_rule_statuses"]) != 14:
        raise FinalizerContractError(f"{name} must contain exact 14-rule status maps")
    for key in ("matched_expectations", "trace_preservation_result", "log_preservation_result", "exact_status_match", "verdict_match", "evaluator_contract_match"):
        if scenario.get(key) is not True:
            raise FinalizerContractError(f"{name} requires {key}=true")


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


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("signoz_api_key", re.compile(r"\bSIGNOZ_API_KEY\s*=\s*['\"]?(?!<|example|fake|your-)([^'\"\s#]+)")),
    ("authorization_header", re.compile(r"(?i)\bAuthorization\s*:\s*(?!<redacted>|<set>|example|fake)(.+)")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+(?!<redacted>|example|fake)([A-Za-z0-9._~+/=-]{16,})")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("cookie", re.compile(r"(?i)\bCookie\s*:\s*(?!<redacted>|example|fake)(.+)")),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*=\s*['\"](?!<redacted>|example|fake)([^'\"]{8,})['\"]")),
    ("service_account", re.compile(r"(?i)\"(?:client_email|private_key|private_key_id)\"\s*:\s*\"(?!<redacted>|example|fake)[^\"]+\"")),
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
        for line_number, line in enumerate(text.splitlines(), 1):
            if is_safe_fixture_line(rel, line):
                continue
            for category, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append({"path": rel.replace("\\", "/"), "line": line_number, "category": category})
    return {
        "scanned_tracked_file_count": len(files),
        "findings_count": len(findings),
        "findings": findings,
        "passed": not findings,
        "sanitized": True,
    }


def is_safe_fixture_line(path: str, line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ("<redacted>", "<set>", "<your-", "example", "fake", "secret", "synthetic")) and (
        path.endswith(".example") or "test" in path.replace("\\", "/").lower() or "readme" in path.lower()
    )


def build_evidence(summary: dict[str, Any], env: dict[str, Any], command_results: list[VerificationCommandResult], secret_scan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    batch_id = str(summary["batch_id"])
    compat = compatibility_contract()
    log_contract = log_api_contract(str(env.get("signoz_version") or "unknown")) | {
        "opentelemetry_api_import_path_used": compat.get("logger_provider_path"),
        "opentelemetry_otlp_exporter_import_path_used": compat.get("exporter_path"),
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
        "gate3b_complete": True,
        "evidence_batch_id": batch_id,
        "ruleset_version": GATE3B_RULESET_VERSION,
        "authoritative_trace_source": AUTHORITATIVE_TRACE_SOURCE,
        "authoritative_log_source": AUTHORITATIVE_LOG_SOURCE,
        "trace_export_verified": True,
        "log_export_verified": True,
        "trace_retrieval_verified": True,
        "log_retrieval_verified": True,
        "full_preservation_verified": True,
        "run_bundle_validation_verified": True,
        "tg_tel_003b_verified": True,
        "tg_tel_008_verified": True,
        "exact_status_maps_verified": True,
        "all_scenarios_matched": True,
        "all_tests_passed": True,
        "secret_scan_passed": secret_scan["passed"],
        "live_exit_code": summary.get("live_exit_code"),
        "finalizer_exit_code": 0,
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
