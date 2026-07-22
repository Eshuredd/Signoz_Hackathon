from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from models import (
    CapabilityAssessment,
    CapabilityState,
    ComparisonReport,
    ComparisonRow,
    FieldAssessment,
    FieldState,
    ProbeEvidence,
    REQUIRED_FIELDS,
    Source,
)


TRACE_API_AUTHORITATIVE = "TRACE_API_AUTHORITATIVE"
MCP_CAN_BE_AUTHORITATIVE = "MCP_CAN_BE_AUTHORITATIVE"
HYBRID_REQUIRES_MORE_EVIDENCE = "HYBRID_REQUIRES_MORE_EVIDENCE"


def compare_sources(trace_api: ProbeEvidence, mcp: ProbeEvidence) -> ComparisonReport:
    rows = build_rows(trace_api, mcp)
    recommendation, reason, provisional = recommend(trace_api, mcp)
    return ComparisonReport(
        generated_at=datetime.now(UTC),
        trace_api=trace_api,
        mcp=mcp,
        rows=rows,
        recommendation=recommendation,
        recommendation_reason=reason,
        provisional_evaluator_source=provisional,
    )


def build_rows(trace_api: ProbeEvidence, mcp: ProbeEvidence) -> list[ComparisonRow]:
    rows = [
        ComparisonRow(
            "direct trace lookup",
            trace_api.direct_lookup.to_cell(),
            mcp.direct_lookup.to_cell(),
        ),
        ComparisonRow(
            "attribute-based trace search",
            trace_api.attribute_search.to_cell(),
            mcp.attribute_search.to_cell(),
        ),
    ]

    trace_api_fields = field_map(trace_api.field_assessments)
    mcp_fields = field_map(mcp.field_assessments)
    for field in REQUIRED_FIELDS:
        rows.append(
            ComparisonRow(
                f"{field} available",
                field_cell(trace_api_fields.get(field)),
                field_cell(mcp_fields.get(field)),
            )
        )

    rows.extend(
        [
            ComparisonRow(
                "preserves multiple spans",
                trace_api.preserves_multiple_spans.to_cell(),
                mcp.preserves_multiple_spans.to_cell(),
            ),
            ComparisonRow(
                "preserves parent-child relationships",
                trace_api.preserves_parent_child.to_cell(),
                mcp.preserves_parent_child.to_cell(),
            ),
            ComparisonRow(
                "suitable for deterministic evaluation",
                trace_api.deterministic_evaluation.to_cell(),
                mcp.deterministic_evaluation.to_cell(),
            ),
            ComparisonRow(
                "suitable for human explanation",
                trace_api.human_explanation.to_cell(),
                mcp.human_explanation.to_cell(),
            ),
            ComparisonRow(
                "authentication required",
                trace_api.authentication_required.to_cell(),
                mcp.authentication_required.to_cell(),
            ),
            ComparisonRow(
                "error behavior",
                trace_api.error_behavior.to_cell(),
                mcp.error_behavior.to_cell(),
            ),
            ComparisonRow(
                "response stability",
                trace_api.response_stability.to_cell(),
                mcp.response_stability.to_cell(),
            ),
        ]
    )
    return rows


def field_map(assessments: list[FieldAssessment]) -> dict[str, FieldAssessment]:
    return {assessment.field: assessment for assessment in assessments}


def field_cell(assessment: FieldAssessment | None) -> str:
    if assessment is None:
        return FieldState.UNAVAILABLE.value
    if assessment.notes:
        return f"{assessment.state.value} - {assessment.notes}"
    return assessment.state.value


def recommend(
    trace_api: ProbeEvidence,
    mcp: ProbeEvidence,
) -> tuple[str, str, str]:
    if mcp_can_be_authoritative(mcp):
        return (
            MCP_CAN_BE_AUTHORITATIVE,
            (
                "MCP returned complete, stable, machine-readable structured telemetry "
                "with required evaluator fields and validated multi-span parent-child evidence."
            ),
            Source.MCP.value,
        )

    if trace_api.trace is None or not trace_api.trace.has_all_required_fields():
        return (
            HYBRID_REQUIRES_MORE_EVIDENCE,
            (
                "Trace API evidence is incomplete or unavailable, so no authoritative "
                "Gate 2 telemetry source is currently usable."
            ),
            "none",
        )

    if not mcp.available or mcp.blocker:
        return (
            HYBRID_REQUIRES_MORE_EVIDENCE,
            (
                "MCP could not be runtime-tested after a supported setup/start attempt; "
                "use the Trace API provisionally until MCP is enabled and complete "
                "structured span data is observed."
            ),
            Source.TRACE_API.value,
        )

    return (
        TRACE_API_AUTHORITATIVE,
        (
            "MCP was reachable but did not provide complete structured span data "
            "for deterministic evaluator use."
        ),
        Source.TRACE_API.value,
    )


def mcp_can_be_authoritative(mcp: ProbeEvidence) -> bool:
    return (
        mcp.available
        and mcp.trace is not None
        and mcp.trace.has_all_required_fields()
        and mcp.response_classification == "complete structured telemetry"
        and mcp.deterministic_evaluation.state == CapabilityState.OBSERVED
        and mcp.response_stability.state == CapabilityState.OBSERVED
        and mcp.preserves_multiple_spans.state == CapabilityState.OBSERVED
        and mcp.preserves_parent_child.state == CapabilityState.OBSERVED
        and (
            mcp.direct_lookup.state == CapabilityState.OBSERVED
            or mcp.attribute_search.state == CapabilityState.OBSERVED
        )
        and not mcp.errors
        and not mcp.blocker
    )


def exit_code_for_report(report: ComparisonReport) -> int:
    if report.trace_api.trace is None or not report.trace_api.trace.has_all_required_fields():
        return 1
    if report.recommendation == HYBRID_REQUIRES_MORE_EVIDENCE:
        return 2
    return 0


def write_report(path: Path, report: ComparisonReport) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str) + "\n")
    return str(path)


def render_report(report: ComparisonReport) -> str:
    lines = [
        "# TraceGuard Gate 2 Comparison",
        "",
        f"Generated at: {report.generated_at.isoformat()}",
        "",
        "## Capability Matrix",
        "",
        "| Capability | SigNoz Trace API | SigNoz MCP | Notes |",
        "|---|---|---|---|",
    ]
    for row in report.rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(row.capability),
                    escape_table(row.trace_api),
                    escape_table(row.mcp),
                    escape_table(row.notes),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Final Decision",
            "",
            report.recommendation,
            "",
            report.recommendation_reason,
            "",
            f"Provisional evaluator source: {report.provisional_evaluator_source}",
        ]
    )

    if report.mcp.blocker:
        lines.extend(["", "## MCP Blocker", "", report.mcp.blocker])
    if report.mcp.smallest_unblock:
        lines.extend(["", "Smallest unblock:", "", report.mcp.smallest_unblock])

    if report.trace_api.errors or report.mcp.errors:
        lines.extend(["", "## Observed Errors", ""])
        for source_name, errors in (
            (Source.TRACE_API.value, report.trace_api.errors),
            (Source.MCP.value, report.mcp.errors),
        ):
            for error in errors:
                lines.append(f"- {source_name}: {error}")

    return "\n".join(lines) + "\n"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
