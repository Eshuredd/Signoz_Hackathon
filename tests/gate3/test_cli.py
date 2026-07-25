from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


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


def test_cli_does_not_import_network_libraries() -> None:
    completed = run_cli("evaluate", "gate3/fixtures/valid/valid_single_span.json")

    assert completed.returncode == 0
    assert completed.stderr == ""
