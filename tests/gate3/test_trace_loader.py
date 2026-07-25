from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate3.models import (
    SUPPORTED_EXPECTATION_SCHEMA_VERSION,
    SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION,
    SUPPORTED_TRACE_INPUT_SCHEMA_VERSION,
)
from gate3.trace_loader import RunBundleInputError, TraceInputError, load_run_bundle_payload, load_trace_file, load_trace_payload


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_valid_fixture_loads() -> None:
    trace = load_trace_file(REPO_ROOT / "gate3" / "fixtures" / "trace" / "pass_canonical_agent_trace.json")
    assert trace.schema_version == 1
    assert trace.spans[0].span_id == "1111111111111111"


def test_schema_constants_are_separate() -> None:
    assert SUPPORTED_TRACE_INPUT_SCHEMA_VERSION == 1
    assert SUPPORTED_EXPECTATION_SCHEMA_VERSION == 1
    assert SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION == 1


def test_schema_version_one_loads_as_real_integer() -> None:
    trace = load_trace_payload({"schema_version": 1, "trace": {"trace_id": "a" * 32, "spans": []}})
    assert type(trace.schema_version) is int


def test_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(TraceInputError, match="Unable to read"):
        load_trace_file(tmp_path / "missing.json")


def test_invalid_json_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TraceInputError, match="Invalid JSON"):
        load_trace_file(path)


@pytest.mark.parametrize("schema_version", [True, False, 1.0, "1", None, [], {}])
def test_trace_schema_version_rejects_invalid_types(schema_version: object) -> None:
    with pytest.raises(TraceInputError, match="schema_version must be an integer"):
        load_trace_payload({"schema_version": schema_version, "trace": {"spans": []}})


@pytest.mark.parametrize("schema_version", [-1, 0, 2, 999])
def test_trace_schema_version_rejects_unsupported_integer_values(schema_version: int) -> None:
    with pytest.raises(TraceInputError, match=f"Unsupported trace input schema_version: {schema_version}"):
        load_trace_payload({"schema_version": schema_version, "trace": {"spans": []}})


@pytest.mark.parametrize("duration", [True, False, 1.5, "1000"])
def test_duration_nano_rejects_bool_float_and_string_values(duration: object) -> None:
    with pytest.raises(TraceInputError, match="duration_nano must be an integer"):
        load_trace_payload({"schema_version": 1, "trace": {"trace_id": "a" * 32, "spans": [{"duration_nano": duration}], "source": "fixture", "metadata": {}}})


def test_run_bundle_loader_and_schema_errors() -> None:
    trace = {"schema_version": 1, "trace": {"trace_id": "a" * 32, "spans": []}}
    bundle = load_run_bundle_payload({"schema_version": 1, "agent_run_id": "run-1", "traces": [trace], "logs": [], "metadata": {}})
    assert bundle.agent_run_id == "run-1"
    with pytest.raises(RunBundleInputError, match="schema_version must be an integer"):
        load_run_bundle_payload({"schema_version": True, "agent_run_id": "run-1", "traces": []})
    with pytest.raises(RunBundleInputError, match="Unsupported run bundle schema_version"):
        load_run_bundle_payload({"schema_version": 2, "agent_run_id": "run-1", "traces": []})


def test_non_object_span_from_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 1, "trace": {"spans": [None]}}), encoding="utf-8")
    with pytest.raises(TraceInputError, match="spans\\[0\\]"):
        load_trace_file(path)
