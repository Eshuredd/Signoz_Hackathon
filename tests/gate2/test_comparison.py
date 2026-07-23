from __future__ import annotations

from datetime import UTC, datetime, timedelta

from comparison import (
    HYBRID_REQUIRES_MORE_EVIDENCE,
    MCP_CAN_BE_AUTHORITATIVE,
    TRACE_API_AUTHORITATIVE,
    compare_sources,
    exit_code_for_report,
)
from models import (
    CapabilityAssessment,
    CapabilityState,
    ProbeEvidence,
    Source,
    Span,
    Trace,
    classify_trace_structure,
    deterministic_assessment,
    relationship_capabilities,
)


def span(span_id: str, parent_span_id: str | None = "") -> Span:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return Span(
        trace_id="a" * 32,
        span_id=span_id,
        parent_span_id=parent_span_id,
        span_name=span_id,
        start_time=start,
        end_time=start + timedelta(milliseconds=1),
        duration_nano=1_000_000,
        status={"code": "OK"},
        attributes={
            "agent.run_id": "run-1",
            "traceguard.run_id": "run-1",
            "traceguard.project": "TraceGuard",
            "traceguard.gate": "1A",
        },
        resource_attributes={"service.name": "svc"},
        service_name="svc",
    )


def evidence(source: Source, trace: Trace | None, available: bool = True) -> ProbeEvidence:
    item = ProbeEvidence(source=source, available=available, trace=trace)
    if trace is not None:
        item.field_assessments = trace.field_assessments()
        item.response_classification = classify_trace_structure(trace)
        item.deterministic_evaluation = deterministic_assessment(trace)
        item.preserves_multiple_spans, item.preserves_parent_child = relationship_capabilities(trace)
        item.direct_lookup = CapabilityAssessment("direct trace lookup", CapabilityState.OBSERVED)
        item.retrieval_workflow = CapabilityAssessment(
            "retrieval workflow completeness",
            CapabilityState.OBSERVED,
        )
    return item


def complete_trace(two_spans: bool = False) -> Trace:
    spans = [span("root", "")]
    if two_spans:
        spans.append(span("child", "root"))
    return Trace("a" * 32, spans, datetime.now(UTC), Source.TRACE_API)


def test_mcp_complete_and_stable_can_be_authoritative() -> None:
    trace_api = evidence(Source.TRACE_API, complete_trace())
    mcp_trace = complete_trace(two_spans=True)
    mcp_trace.source = Source.MCP
    mcp = evidence(Source.MCP, mcp_trace)
    mcp.response_stability = CapabilityAssessment("response stability", CapabilityState.OBSERVED)

    report = compare_sources(trace_api, mcp)

    assert report.recommendation == MCP_CAN_BE_AUTHORITATIVE
    assert exit_code_for_report(report) == 0


def test_mcp_reachable_but_incomplete_uses_trace_api_authoritative() -> None:
    trace_api = evidence(Source.TRACE_API, complete_trace())
    mcp = evidence(Source.MCP, complete_trace(), available=True)

    report = compare_sources(trace_api, mcp)

    assert report.recommendation == TRACE_API_AUTHORITATIVE
    assert exit_code_for_report(report) == 0


def test_mcp_unavailable_after_attempt_is_hybrid_exit_2() -> None:
    trace_api = evidence(Source.TRACE_API, complete_trace())
    mcp = ProbeEvidence(source=Source.MCP, available=False, blocker="Docker daemon unavailable")

    report = compare_sources(trace_api, mcp)

    assert report.recommendation == HYBRID_REQUIRES_MORE_EVIDENCE
    assert report.provisional_evaluator_source == Source.TRACE_API.value
    assert exit_code_for_report(report) == 2


def test_trace_api_unusable_exits_1() -> None:
    trace_api = ProbeEvidence(source=Source.TRACE_API, available=False)
    mcp = ProbeEvidence(source=Source.MCP, available=False)

    report = compare_sources(trace_api, mcp)

    assert exit_code_for_report(report) == 1


def test_mcp_errors_prevent_authoritative_even_with_complete_trace() -> None:
    trace_api = evidence(Source.TRACE_API, complete_trace())
    mcp_trace = complete_trace(two_spans=True)
    mcp_trace.source = Source.MCP
    mcp = evidence(Source.MCP, mcp_trace)
    mcp.response_stability = CapabilityAssessment("response stability", CapabilityState.OBSERVED)
    mcp.errors.append("mcp_response_stability: changed shape")

    report = compare_sources(trace_api, mcp)

    assert report.recommendation == TRACE_API_AUTHORITATIVE


def test_root_only_mcp_cannot_be_authoritative() -> None:
    trace_api = evidence(Source.TRACE_API, complete_trace())
    mcp = evidence(Source.MCP, complete_trace())
    mcp.response_stability = CapabilityAssessment("response stability", CapabilityState.OBSERVED)

    report = compare_sources(trace_api, mcp)

    assert report.recommendation == TRACE_API_AUTHORITATIVE


def test_configured_trace_api_attribute_search_unresolved_keeps_comparison_provisional() -> None:
    trace_api = evidence(Source.TRACE_API, complete_trace())
    trace_api.retrieval_workflow = CapabilityAssessment(
        "retrieval workflow completeness",
        CapabilityState.NOT_OBSERVED,
        "configured agent.run_id discovery unresolved",
    )
    mcp = evidence(Source.MCP, complete_trace(), available=True)

    report = compare_sources(trace_api, mcp)

    assert report.recommendation == HYBRID_REQUIRES_MORE_EVIDENCE
    assert report.provisional_evaluator_source == Source.TRACE_API.value
