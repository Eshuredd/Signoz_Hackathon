from __future__ import annotations

from datetime import UTC, datetime, timedelta

from models import (
    CapabilityState,
    FieldState,
    Source,
    Span,
    Trace,
    classify_trace_structure,
    deterministic_assessment,
    relationship_capabilities,
)


def span(
    *,
    trace_id: str = "0" * 32,
    span_id: str = "1",
    parent_span_id: str | None = "",
    name: str = "span",
    attributes: dict[str, object] | None = None,
    resource: dict[str, object] | None = None,
) -> Span:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        span_name=name,
        start_time=start,
        end_time=start + timedelta(milliseconds=1),
        duration_nano=1_000_000,
        status={"status_code": 0},
        attributes=attributes
        if attributes is not None
        else {
            "agent.run_id": "run-1",
            "traceguard.run_id": "run-1",
            "traceguard.project": "TraceGuard",
            "traceguard.gate": "1A",
            "unknown.attr": "kept",
        },
        resource_attributes=resource if resource is not None else {"service.name": "svc"},
        service_name="svc",
    )


def trace(spans: list[Span]) -> Trace:
    return Trace(
        trace_id=spans[0].trace_id if spans else "",
        spans=spans,
        retrieved_at=datetime.now(UTC),
        source=Source.TRACE_API,
    )


def assessment_state(t: Trace, field: str) -> FieldState:
    return {item.field: item.state for item in t.field_assessments()}[field]


def test_complete_expected_attributes_are_present_and_unknown_keys_retained() -> None:
    t = trace([span()])

    assert assessment_state(t, "complete attributes") == FieldState.PRESENT
    assert t.spans[0].attributes["unknown.attr"] == "kept"
    assert t.has_all_required_fields()


def test_empty_attributes_are_absent_not_complete() -> None:
    t = trace([span(attributes={})])

    item = next(a for a in t.field_assessments() if a.field == "complete attributes")
    assert item.state == FieldState.ABSENT
    assert "agent.run_id" in item.notes
    assert not t.has_all_required_fields()


def test_one_missing_required_attribute_is_partial() -> None:
    attrs = {
        "agent.run_id": "run-1",
        "traceguard.run_id": "run-1",
        "traceguard.project": "TraceGuard",
    }
    t = trace([span(attributes=attrs)])

    item = next(a for a in t.field_assessments() if a.field == "complete attributes")
    assert item.state == FieldState.PARTIAL
    assert "traceguard.gate" in item.notes


def test_root_only_trace_does_not_prove_relationship_preservation() -> None:
    t = trace([span(parent_span_id="")])

    multiple, parent_child = relationship_capabilities(t)
    assert multiple.state == CapabilityState.NOT_OBSERVED
    assert parent_child.state == CapabilityState.NOT_OBSERVED
    assert not t.has_valid_parent_child_relationship()


def test_valid_root_child_trace_proves_relationship_preservation() -> None:
    trace_id = "a" * 32
    root = span(trace_id=trace_id, span_id="root", parent_span_id="", name="gate2.test.root")
    child = span(
        trace_id=trace_id,
        span_id="child",
        parent_span_id="root",
        name="gate2.test.child",
    )
    t = trace([root, child])

    multiple, parent_child = relationship_capabilities(t)
    assert multiple.state == CapabilityState.OBSERVED
    assert parent_child.state == CapabilityState.OBSERVED
    assert t.has_valid_parent_child_relationship()


def test_broken_parent_reference_does_not_prove_parent_child() -> None:
    t = trace([span(span_id="root", parent_span_id=""), span(span_id="child", parent_span_id="missing")])

    _, parent_child = relationship_capabilities(t)
    assert parent_child.state == CapabilityState.NOT_OBSERVED


def test_incomplete_fields_fail_deterministic_suitability() -> None:
    t = trace([span(attributes={})])

    assert classify_trace_structure(t) == "partially structured telemetry"
    assert deterministic_assessment(t).state == CapabilityState.FAILED
