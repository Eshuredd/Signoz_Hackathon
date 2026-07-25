from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate3.trace_loader import TraceInputError, load_trace_file, load_trace_payload


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_valid_fixture_loads() -> None:
    trace = load_trace_file(REPO_ROOT / "gate3" / "fixtures" / "valid" / "valid_single_span.json")

    assert trace.schema_version == 1
    assert trace.spans[0].span_id == "1111111111111111"


def test_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(TraceInputError, match="Unable to read"):
        load_trace_file(tmp_path / "missing.json")


def test_invalid_json_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(TraceInputError, match="Invalid JSON"):
        load_trace_file(path)


@pytest.mark.parametrize(
    "payload,match",
    [
        ([], "top-level"),
        ({"schema_version": 999, "trace": {"spans": []}}, "Unsupported"),
        ({"schema_version": 1}, "trace object"),
        ({"schema_version": 1, "trace": {"spans": {}}}, "spans must be a list"),
        ({"schema_version": 1, "trace": {"spans": [1]}}, "must be an object"),
        ({"schema_version": 1, "trace": {"spans": [{"start_time": "nope"}]}}, "valid ISO-8601"),
        ({"schema_version": 1, "trace": {"spans": [{"attributes": []}]}}, "attributes must be an object"),
        ({"schema_version": 1, "trace": {"spans": [{"status": []}]}}, "status must be an object"),
        ({"schema_version": 1, "trace": {"spans": [{"span_id": 1}]}}, "span_id must be a string"),
    ],
)
def test_loader_contract_errors(payload: object, match: str) -> None:
    with pytest.raises(TraceInputError, match=match):
        load_trace_payload(payload)


def test_non_object_span_from_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 1, "trace": {"spans": [None]}}), encoding="utf-8")

    with pytest.raises(TraceInputError, match="spans\\[0\\]"):
        load_trace_file(path)


@pytest.mark.parametrize("duration", [True, False, 1.5, "1000"])
def test_duration_nano_rejects_bool_float_and_string_values(duration: object) -> None:
    payload = {
        "schema_version": 1,
        "trace": {
            "trace_id": "a" * 32,
            "spans": [{"duration_nano": duration}],
            "source": "fixture",
            "metadata": {},
        },
    }

    with pytest.raises(TraceInputError, match="duration_nano must be an integer"):
        load_trace_payload(payload)


def test_duration_nano_accepts_valid_integer_zero() -> None:
    trace = load_trace_payload(
        {
            "schema_version": 1,
            "trace": {
                "trace_id": "a" * 32,
                "spans": [{"duration_nano": 0}],
                "source": "fixture",
                "metadata": {},
            },
        }
    )

    assert trace.spans[0].get("duration_nano") == 0
