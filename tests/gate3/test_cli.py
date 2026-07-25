from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from gate3 import cli


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
FORBIDDEN_NETWORK_IMPORTS = {"requests", "httpx", "urllib.request", "urllib3", "aiohttp", "socket", "websockets", "grpc", "opentelemetry.exporter", "gate2.signoz_api_client", "gate2.mcp_probe"}


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PYTHON, "gate3/cli.py", *args], cwd=REPO_ROOT, text=True, capture_output=True, check=False)


def test_evaluate_exit_codes_and_json_output() -> None:
    cases = [
        ("evaluate-trace", "gate3/fixtures/trace/pass_canonical_agent_trace.json", 0, "PASS"),
        ("evaluate-trace", "gate3/fixtures/trace/pass_with_warnings_missing_token_usage.json", 10, "PASS_WITH_WARNINGS"),
        ("evaluate-trace", "gate3/fixtures/trace/block_missing_agent_attributes.json", 20, "BLOCK"),
        ("evaluate-run", "gate3/fixtures/run/pass_with_warnings_uncorrelated_logs.json", 10, "PASS_WITH_WARNINGS"),
    ]
    for command, fixture, code, verdict in cases:
        completed = run_cli(command, fixture)
        assert completed.returncode == code
        output = json.loads(completed.stdout)
        assert output["verdict"] == verdict
        assert len(output["rule_results"]) == 14
        assert output["verdict"] != "WARN"


def test_invalid_input_returns_2(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")
    completed = run_cli("evaluate-trace", str(path))
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_type"] == "TraceInputError"


def test_empty_run_bundle_cli_returns_2(tmp_path: Path) -> None:
    path = tmp_path / "empty-run.json"
    path.write_text(json.dumps({"schema_version": 1, "agent_run_id": "run-1", "traces": [], "logs": []}), encoding="utf-8")
    completed = run_cli("evaluate-run", str(path))
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_type"] == "RunBundleInputError"


def test_evaluate_all_validate_and_list_rules() -> None:
    evaluate_all = run_cli("evaluate-all")
    validate = run_cli("validate-fixtures")
    list_rules = run_cli("list-rules")
    assert evaluate_all.returncode == 0
    assert json.loads(evaluate_all.stdout)["fixture_expectation_matches"] is True
    assert validate.returncode == 0
    rules = json.loads(list_rules.stdout)["rules"]
    assert list_rules.returncode == 0
    assert len(rules) == 14


def test_duplicate_key_manifest_returns_2(tmp_path: Path) -> None:
    manifest = tmp_path / "expectations.json"
    manifest.write_text('{"schema_version":1,"fixtures":{"x":{"verdict":"PASS","rule_statuses":{}},"x":{"verdict":"PASS","rule_statuses":{}}}}', encoding="utf-8")
    completed = run_cli("validate-fixtures")
    assert completed.returncode == 0
    with pytest.raises(cli.ExpectationError, match="Duplicate JSON object key"):
        cli.load_expectations(REPO_ROOT / "gate3" / "fixtures" / "trace", manifest)


@pytest.mark.parametrize("schema_version_json", ["true", "1.0"])
def test_manifest_schema_version_type_errors(schema_version_json: str, tmp_path: Path) -> None:
    manifest = tmp_path / "expectations.json"
    manifest.write_text(f'{{"schema_version":{schema_version_json},"fixtures":{{}}}}', encoding="utf-8")
    with pytest.raises(cli.ExpectationError, match="schema_version must be an integer"):
        cli.load_expectations(REPO_ROOT / "gate3" / "fixtures" / "trace", manifest)


def test_expectation_verdict_must_match_rule_statuses() -> None:
    statuses = {rule.rule_id: "PASSED" for rule in cli.RULES}
    assert cli.verdict_from_expected_statuses(statuses) == "PASS"
    statuses["TG-TEL-002"] = "FAILED"
    assert cli.verdict_from_expected_statuses(statuses) == "BLOCK"
    statuses["TG-TEL-002"] = "PASSED"
    statuses["TG-TEL-006"] = "FAILED"
    assert cli.verdict_from_expected_statuses(statuses) == "PASS_WITH_WARNINGS"


def test_gate3_production_code_has_no_forbidden_network_imports() -> None:
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "gate3").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for imported in imported_modules(node):
                if any(imported == forbidden or imported.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_NETWORK_IMPORTS):
                    violations.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {imported}")
    assert violations == []


def imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        modules = [module] if module else []
        modules.extend(f"{module}.{alias.name}" for alias in node.names if module)
        return modules
    return []


def test_cli_workflows_pass_with_runtime_network_denied(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def deny_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 3 attempted network access.")

    monkeypatch.setattr(socket, "socket", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    assert cli.main(["evaluate-trace", "gate3/fixtures/trace/pass_canonical_agent_trace.json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"
    assert cli.main(["evaluate-all"]) == 0
