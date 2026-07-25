from __future__ import annotations

from copy import deepcopy

from gate3.models import NormalizedTrace, Span
from gate3.rules import RULE_BY_ID
from gate3.trace_loader import load_trace_payload


TRACE_ID = "abcabcabcabcabcabcabcabcabcabcab"


def payload(span_overrides: dict[str, object] | None = None, *, spans: list[dict[str, object]] | None = None) -> dict[str, object]:
    base_span: dict[str, object] = {
        "trace_id": TRACE_ID,
        "span_id": "1111111111111111",
        "parent_span_id": None,
        "span_name": "agent.run",
        "start_time": "2026-07-25T08:00:00Z",
        "end_time": "2026-07-25T08:00:01Z",
        "duration_nano": 1000000000,
        "status": {"status_code": "OK"},
        "attributes": {
            "agent.run_id": "run-1",
            "traceguard.run_id": "run-1",
            "traceguard.project": "TraceGuard",
            "traceguard.gate": "3A",
        },
        "resource_attributes": {"service.name": "svc"},
        "service_name": "svc",
    }
    if span_overrides:
        base_span.update(span_overrides)
    return {"schema_version": 1, "trace": {"trace_id": TRACE_ID, "spans": spans if spans is not None else [base_span], "source": "fixture", "metadata": {}}}


def findings(rule_id: str, trace_payload: dict[str, object]) -> list[object]:
    return RULE_BY_ID[rule_id].evaluate(load_trace_payload(trace_payload))


def test_tg_tel_001_detects_empty_trace() -> None:
    assert findings("TG-TEL-001", payload(spans=[]))


def test_tg_tel_002_detects_each_missing_identity_field() -> None:
    result = findings("TG-TEL-002", payload({"trace_id": "", "span_id": "", "span_name": ""}))
    assert result[0].evidence["missing_fields"] == ["trace_id", "span_id", "span_name"]


def test_tg_tel_003_detects_trace_mismatch() -> None:
    assert findings("TG-TEL-003", payload({"trace_id": "b" * 32}))


def test_tg_tel_004_detects_duplicate_span_ids() -> None:
    span_a = payload()["trace"]["spans"][0]  # type: ignore[index]
    span_b = deepcopy(span_a)
    span_b["parent_span_id"] = "1111111111111111"
    assert findings("TG-TEL-004", payload(spans=[span_a, span_b]))[0].evidence["occurrence_count"] == 2


def test_tg_tel_005_accepts_one_root_and_detects_zero_or_multiple_roots() -> None:
    assert findings("TG-TEL-005", payload()) == []
    assert findings("TG-TEL-005", payload({"parent_span_id": "missing"}))
    span_a = payload()["trace"]["spans"][0]  # type: ignore[index]
    span_b = deepcopy(span_a)
    span_b["span_id"] = "2222222222222222"
    span_b["parent_span_id"] = ""
    assert findings("TG-TEL-005", payload(spans=[span_a, span_b]))


def test_tg_tel_006_detects_orphan_parent() -> None:
    assert findings("TG-TEL-006", payload({"parent_span_id": "missing"}))


def test_tg_tel_007_detects_missing_timing_fields() -> None:
    span = payload()["trace"]["spans"][0]  # type: ignore[index]
    for field in ("start_time", "end_time", "duration_nano"):
        item = deepcopy(span)
        item.pop(field)
        assert findings("TG-TEL-007", payload(spans=[item]))


def test_tg_tel_008_detects_invalid_timing() -> None:
    assert findings("TG-TEL-008", payload({"duration_nano": -1}))
    assert findings("TG-TEL-008", payload({"start_time": "2026-07-25T08:00:02Z", "end_time": "2026-07-25T08:00:01Z"}))


def test_tg_tel_008_does_not_treat_boolean_duration_as_integer() -> None:
    trace = NormalizedTrace(
        schema_version=1,
        trace_id=TRACE_ID,
        spans=(
            Span(
                raw={
                    "trace_id": TRACE_ID,
                    "span_id": "1111111111111111",
                    "parent_span_id": None,
                    "span_name": "agent.run",
                    "start_time": "2026-07-25T08:00:00Z",
                    "end_time": "2026-07-25T08:00:01Z",
                    "duration_nano": False,
                    "attributes": {},
                    "resource_attributes": {},
                    "status": {},
                },
                index=0,
            ),
        ),
        retrieved_at=None,
        source="fixture",
    )

    assert RULE_BY_ID["TG-TEL-008"].evaluate(trace) == []


def test_tg_tel_009_detects_missing_service_and_accepts_resource_service() -> None:
    assert findings("TG-TEL-009", payload({"service_name": "", "resource_attributes": {}}))
    assert findings("TG-TEL-009", payload({"service_name": "", "resource_attributes": {"service.name": "svc"}})) == []


def test_tg_tel_010_and_011_detect_missing_root_run_ids() -> None:
    assert findings("TG-TEL-010", payload({"attributes": {"traceguard.run_id": "run-1", "traceguard.project": "TraceGuard", "traceguard.gate": "3A"}}))
    assert findings("TG-TEL-011", payload({"attributes": {"agent.run_id": "run-1", "traceguard.project": "TraceGuard", "traceguard.gate": "3A"}}))


def test_tg_tel_012_detects_run_id_mismatches_and_allows_child_omission() -> None:
    assert findings("TG-TEL-012", payload({"attributes": {"agent.run_id": "a", "traceguard.run_id": "b", "traceguard.project": "TraceGuard", "traceguard.gate": "3A"}}))
    root = payload()["trace"]["spans"][0]  # type: ignore[index]
    child = deepcopy(root)
    child["span_id"] = "2222222222222222"
    child["parent_span_id"] = "1111111111111111"
    child["attributes"] = {"agent.run_id": "child"}
    assert findings("TG-TEL-012", payload(spans=[root, child]))
    child["attributes"] = {}
    assert findings("TG-TEL-012", payload(spans=[root, child])) == []


def test_tg_tel_013_detects_missing_project_and_gate() -> None:
    result = findings("TG-TEL-013", payload({"attributes": {"agent.run_id": "run-1", "traceguard.run_id": "run-1"}}))
    assert result[0].evidence["missing_context_attributes"] == ["traceguard.project", "traceguard.gate"]


def test_root_only_rules_skip_multiple_roots() -> None:
    first = payload()["trace"]["spans"][0]  # type: ignore[index]
    second = deepcopy(first)
    second["span_id"] = "2222222222222222"
    assert findings("TG-TEL-010", payload(spans=[first, second])) == []
    assert findings("TG-TEL-011", payload(spans=[first, second])) == []
    assert findings("TG-TEL-012", payload(spans=[first, second])) == []
    assert findings("TG-TEL-013", payload(spans=[first, second])) == []
