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
FORBIDDEN_NETWORK_IMPORTS = {
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "aiohttp",
    "socket",
    "websockets",
    "grpc",
    "opentelemetry.exporter",
    "gate2.signoz_api_client",
    "gate2.mcp_probe",
}


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "gate3/cli.py", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_evaluate_exit_codes_and_json_output() -> None:
    cases = [
        ("gate3/fixtures/valid/valid_single_span.json", 0),
        ("gate3/fixtures/warn/warn_missing_service_name.json", 10),
        ("gate3/fixtures/block/block_missing_agent_run_id.json", 20),
    ]
    for fixture, code in cases:
        completed = run_cli("evaluate", fixture)
        assert completed.returncode == code
        assert json.loads(completed.stdout)["verdict"] in {"PASS", "WARN", "BLOCK"}


def test_invalid_input_returns_2(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")

    completed = run_cli("evaluate", str(path))

    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_type"] == "TraceInputError"


def test_evaluate_all_and_validate_fixtures_return_zero() -> None:
    evaluate_all = run_cli("evaluate-all", "gate3/fixtures")
    validate = run_cli("validate-fixtures", "gate3/fixtures")

    assert evaluate_all.returncode == 0
    assert json.loads(evaluate_all.stdout)["fixture_expectation_matches"] is True
    assert validate.returncode == 0
    assert json.loads(validate.stdout)["fixtures_valid"] is True


def test_validate_fixtures_returns_2_for_duplicate_key_manifest(tmp_path: Path) -> None:
    manifest = duplicate_key_manifest(tmp_path)

    completed = run_cli("validate-fixtures", "gate3/fixtures", "--expectations", str(manifest))

    assert completed.returncode == 2
    output = json.loads(completed.stdout)
    assert output["error_type"] == "ExpectationError"
    assert "Duplicate JSON object key" in output["message"]


def test_evaluate_all_returns_2_for_duplicate_key_manifest(tmp_path: Path) -> None:
    manifest = duplicate_key_manifest(tmp_path)

    completed = run_cli("evaluate-all", "gate3/fixtures", "--expectations", str(manifest))

    assert completed.returncode == 2
    output = json.loads(completed.stdout)
    assert output["error_type"] == "ExpectationError"
    assert "Duplicate JSON object key" in output["message"]


def duplicate_key_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "expectations.json"
    manifest.write_text(
        """
        {
          "schema_version": 1,
          "fixtures": {
            "valid/valid_single_span.json": {
              "verdict": "PASS",
              "rule_ids": []
            },
            "valid/valid_single_span.json": {
              "verdict": "BLOCK",
              "rule_ids": ["TG-TEL-001"]
            }
          }
        }
        """,
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize("schema_version_json", ["true", "1.0"])
@pytest.mark.parametrize("command", ["validate-fixtures", "evaluate-all"])
def test_fixture_manifest_schema_version_type_errors_return_2(
    tmp_path: Path,
    command: str,
    schema_version_json: str,
) -> None:
    manifest = tmp_path / "expectations.json"
    manifest.write_text(
        f"""
        {{
          "schema_version": {schema_version_json},
          "fixtures": {{}}
        }}
        """,
        encoding="utf-8",
    )

    completed = run_cli(command, "gate3/fixtures", "--expectations", str(manifest))

    assert completed.returncode == 2
    assert completed.stderr == ""
    output = json.loads(completed.stdout)
    assert output["error_type"] == "ExpectationError"
    assert "schema_version" in output["message"]


def test_gate3_production_code_has_no_forbidden_network_imports() -> None:
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "gate3").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for imported in imported_modules(node):
                if is_forbidden_network_import(imported):
                    rel_path = path.relative_to(REPO_ROOT).as_posix()
                    violations.append(f"{rel_path}: {imported}")

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


def is_forbidden_network_import(imported: str) -> bool:
    return any(imported == forbidden or imported.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_NETWORK_IMPORTS)


def test_cli_workflows_pass_with_runtime_network_denied(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def deny_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 3A attempted network access.")

    monkeypatch.setattr(socket, "socket", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        monkeypatch.setattr(requests, "request", deny_network)
        monkeypatch.setattr(requests.Session, "request", deny_network)

    cases = [
        (["evaluate", "gate3/fixtures/valid/valid_single_span.json"], 0, "PASS"),
        (["evaluate", "gate3/fixtures/warn/warn_missing_service_name.json"], 10, "WARN"),
        (["evaluate", "gate3/fixtures/block/block_missing_agent_run_id.json"], 20, "BLOCK"),
        (["validate-fixtures", "gate3/fixtures"], 0, None),
        (["evaluate-all", "gate3/fixtures"], 0, None),
    ]
    for args, exit_code, verdict in cases:
        assert cli.main(args) == exit_code
        output = json.loads(capsys.readouterr().out)
        if verdict is not None:
            assert output["verdict"] == verdict
