from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


REQUIRED_FIELDS = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "span_name",
    "start_time",
    "end_time",
    "duration",
    "status",
    "complete attributes",
    "resource attributes",
)

EXPECTED_GATE1A_ATTRIBUTES = (
    "agent.run_id",
    "traceguard.run_id",
    "traceguard.project",
    "traceguard.gate",
)


class Source(str, Enum):
    TRACE_API = "SigNoz Trace API"
    MCP = "SigNoz MCP"


class FieldState(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable from this API"
    TRANSFORMED = "present but transformed"
    PARTIAL = "present only for some spans"


class CapabilityState(str, Enum):
    OBSERVED = "observed"
    NOT_OBSERVED = "not observed"
    NOT_CONFIGURED = "not configured"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class FieldAssessment:
    field: str
    state: FieldState
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "state": self.state.value, "notes": self.notes}


@dataclass(frozen=True)
class CapabilityAssessment:
    capability: str
    state: CapabilityState
    notes: str = ""

    def to_cell(self) -> str:
        if self.notes:
            return f"{self.state.value} - {self.notes}"
        return self.state.value

    def to_dict(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "state": self.state.value,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    span_name: str
    start_time: datetime | None
    end_time: datetime | None
    duration_nano: int | None
    status: dict[str, Any]
    attributes: dict[str, Any]
    resource_attributes: dict[str, Any]
    service_name: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "span_name": self.span_name,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_nano": self.duration_nano,
            "status": self.status,
            "attributes": self.attributes,
            "resource_attributes": self.resource_attributes,
            "service_name": self.service_name,
        }


@dataclass
class Trace:
    trace_id: str
    spans: list[Span]
    retrieved_at: datetime
    source: Source
    raw_artifact: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "spans": [span.to_dict() for span in self.spans],
            "retrieved_at": self.retrieved_at.isoformat(),
            "source": self.source.value,
            "raw_artifact": self.raw_artifact,
            "metadata": self.metadata,
        }

    def field_assessments(self) -> list[FieldAssessment]:
        if not self.spans:
            return [
                FieldAssessment(field, FieldState.ABSENT, "trace has no spans")
                for field in REQUIRED_FIELDS
            ]

        return [
            self._assess_trace_id(),
            self._assess_span_id(),
            self._assess_parent_span_id(),
            self._assess_span_name(),
            self._assess_start_time(),
            self._assess_end_time(),
            self._assess_duration(),
            self._assess_status(),
            self._assess_attributes(),
            self._assess_resource_attributes(),
        ]

    def has_all_required_fields(self) -> bool:
        allowed = {FieldState.PRESENT, FieldState.TRANSFORMED}
        return all(assessment.state in allowed for assessment in self.field_assessments())

    def has_multiple_spans(self) -> bool:
        return len(self.spans) >= 2

    def has_valid_parent_child_relationship(self) -> bool:
        if len(self.spans) < 2:
            return False
        spans_by_id = {span.span_id: span for span in self.spans if span.span_id}
        for span in self.spans:
            if not span.parent_span_id:
                continue
            parent = spans_by_id.get(span.parent_span_id)
            if parent is not None and parent.trace_id == span.trace_id:
                return True
        return False

    def _all(self, predicate: Any) -> bool:
        return all(predicate(span) for span in self.spans)

    def _some(self, predicate: Any) -> bool:
        return any(predicate(span) for span in self.spans)

    def _state_all_some(self, predicate: Any) -> FieldState:
        if self._all(predicate):
            return FieldState.PRESENT
        if self._some(predicate):
            return FieldState.PARTIAL
        return FieldState.ABSENT

    def _assess_trace_id(self) -> FieldAssessment:
        state = self._state_all_some(lambda span: bool(span.trace_id))
        notes = f"{len(self.spans)} span(s) checked"
        return FieldAssessment("trace_id", state, notes)

    def _assess_span_id(self) -> FieldAssessment:
        return FieldAssessment(
            "span_id",
            self._state_all_some(lambda span: bool(span.span_id)),
            f"{len(self.spans)} span(s) checked",
        )

    def _assess_parent_span_id(self) -> FieldAssessment:
        state = self._state_all_some(lambda span: span.parent_span_id is not None)
        root_count = sum(1 for span in self.spans if span.parent_span_id == "")
        child_count = sum(1 for span in self.spans if span.parent_span_id)
        notes = (
            f"{root_count} root span(s) have empty parent_span_id; "
            f"{child_count} child span reference(s) observed"
        )
        return FieldAssessment("parent_span_id", state, notes)

    def _assess_span_name(self) -> FieldAssessment:
        return FieldAssessment(
            "span_name",
            self._state_all_some(lambda span: bool(span.span_name)),
        )

    def _assess_start_time(self) -> FieldAssessment:
        return FieldAssessment(
            "start_time",
            self._state_all_some(lambda span: span.start_time is not None),
        )

    def _assess_end_time(self) -> FieldAssessment:
        return FieldAssessment(
            "end_time",
            self._state_all_some(lambda span: span.end_time is not None),
        )

    def _assess_duration(self) -> FieldAssessment:
        return FieldAssessment(
            "duration",
            self._state_all_some(lambda span: span.duration_nano is not None),
            "duration is normalized as duration_nano",
        )

    def _assess_status(self) -> FieldAssessment:
        return FieldAssessment(
            "status",
            self._state_all_some(lambda span: bool(span.status)),
        )

    def _assess_attributes(self) -> FieldAssessment:
        structural_state = self._state_all_some(lambda span: isinstance(span.attributes, dict))
        if structural_state == FieldState.ABSENT:
            return FieldAssessment(
                "complete attributes",
                FieldState.ABSENT,
                "attribute map unavailable",
            )

        non_empty_state = self._state_all_some(lambda span: bool(span.attributes))
        missing = sorted(
            {
                key
                for key in EXPECTED_GATE1A_ATTRIBUTES
                if any(key not in span.attributes for span in self.spans)
            }
        )
        if missing and non_empty_state == FieldState.ABSENT:
            return FieldAssessment(
                "complete attributes",
                FieldState.ABSENT,
                "attribute map structurally available but empty; missing expected custom attributes: "
                + ", ".join(missing),
            )
        if missing:
            return FieldAssessment(
                "complete attributes",
                FieldState.PARTIAL,
                "attribute map structurally available; missing expected custom attributes: "
                + ", ".join(missing),
            )

        state = FieldState.PRESENT
        if structural_state == FieldState.PARTIAL or non_empty_state == FieldState.PARTIAL:
            state = FieldState.PARTIAL
        notes = "attribute map structurally available; expected Gate 1A attributes present"
        unknown_keys = sorted(
            {
                key
                for span in self.spans
                for key in span.attributes
                if key not in EXPECTED_GATE1A_ATTRIBUTES
            }
        )
        if unknown_keys:
            notes += f"; preserved {len(unknown_keys)} non-required attribute key(s)"
        return FieldAssessment("complete attributes", state, notes)

    def _assess_resource_attributes(self) -> FieldAssessment:
        state = self._state_all_some(
            lambda span: isinstance(span.resource_attributes, dict)
            and bool(span.resource_attributes)
        )
        return FieldAssessment(
            "resource attributes",
            state,
            "resource map includes service metadata when present",
        )


@dataclass(frozen=True)
class TraceSearchHit:
    trace_id: str
    span_id: str | None
    span_name: str | None
    attributes: dict[str, Any]
    resource_attributes: dict[str, Any]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "span_name": self.span_name,
            "attributes": self.attributes,
            "resource_attributes": self.resource_attributes,
            "raw": self.raw,
        }


@dataclass
class ProbeEvidence:
    source: Source
    available: bool
    trace: Trace | None = None
    field_assessments: list[FieldAssessment] = field(default_factory=list)
    direct_lookup: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "direct trace lookup",
            CapabilityState.NOT_OBSERVED,
        )
    )
    attribute_search: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.NOT_OBSERVED,
        )
    )
    preserves_multiple_spans: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "preserves multiple spans",
            CapabilityState.NOT_OBSERVED,
            "no validated multi-span trace observed",
        )
    )
    preserves_parent_child: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "preserves parent-child relationships",
            CapabilityState.NOT_OBSERVED,
            "no validated root-child trace observed",
        )
    )
    deterministic_evaluation: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "suitable for deterministic evaluation",
            CapabilityState.NOT_OBSERVED,
        )
    )
    human_explanation: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "suitable for human explanation",
            CapabilityState.NOT_OBSERVED,
        )
    )
    authentication_required: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "authentication required",
            CapabilityState.NOT_OBSERVED,
        )
    )
    error_behavior: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "error behavior",
            CapabilityState.NOT_OBSERVED,
        )
    )
    response_stability: CapabilityAssessment = field(
        default_factory=lambda: CapabilityAssessment(
            "response stability",
            CapabilityState.NOT_OBSERVED,
        )
    )
    response_classification: str = "not observed"
    raw_artifacts: list[str] = field(default_factory=list)
    commands_attempted: list[str] = field(default_factory=list)
    non_secret_config: dict[str, Any] = field(default_factory=dict)
    installed_signoz_version: str | None = None
    errors: list[str] = field(default_factory=list)
    blocker: str | None = None
    smallest_unblock: str | None = None
    mcp_timebox_reached: bool = False
    observations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "available": self.available,
            "trace": self.trace.to_dict() if self.trace else None,
            "field_assessments": [
                assessment.to_dict() for assessment in self.field_assessments
            ],
            "direct_lookup": self.direct_lookup.to_dict(),
            "attribute_search": self.attribute_search.to_dict(),
            "preserves_multiple_spans": self.preserves_multiple_spans.to_dict(),
            "preserves_parent_child": self.preserves_parent_child.to_dict(),
            "deterministic_evaluation": self.deterministic_evaluation.to_dict(),
            "human_explanation": self.human_explanation.to_dict(),
            "authentication_required": self.authentication_required.to_dict(),
            "error_behavior": self.error_behavior.to_dict(),
            "response_stability": self.response_stability.to_dict(),
            "response_classification": self.response_classification,
            "raw_artifacts": self.raw_artifacts,
            "commands_attempted": self.commands_attempted,
            "non_secret_config": self.non_secret_config,
            "installed_signoz_version": self.installed_signoz_version,
            "errors": self.errors,
            "blocker": self.blocker,
            "smallest_unblock": self.smallest_unblock,
            "mcp_timebox_reached": self.mcp_timebox_reached,
            "observations": self.observations,
        }


@dataclass(frozen=True)
class ComparisonRow:
    capability: str
    trace_api: str
    mcp: str
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "trace_api": self.trace_api,
            "mcp": self.mcp,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ComparisonReport:
    generated_at: datetime
    trace_api: ProbeEvidence
    mcp: ProbeEvidence
    rows: list[ComparisonRow]
    recommendation: str
    recommendation_reason: str
    provisional_evaluator_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "trace_api": self.trace_api.to_dict(),
            "mcp": self.mcp.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "recommendation": self.recommendation,
            "recommendation_reason": self.recommendation_reason,
            "provisional_evaluator_source": self.provisional_evaluator_source,
        }


def now_utc() -> datetime:
    return datetime.now(UTC)


def classify_trace_structure(trace: Trace) -> str:
    if trace.has_all_required_fields():
        return "complete structured telemetry"
    if any(
        assessment.state in {FieldState.PRESENT, FieldState.PARTIAL, FieldState.TRANSFORMED}
        for assessment in trace.field_assessments()
    ):
        return "partially structured telemetry"
    return "summarized telemetry or natural-language explanation"


def deterministic_assessment(trace: Trace) -> CapabilityAssessment:
    if trace.has_all_required_fields():
        return CapabilityAssessment(
            "suitable for deterministic evaluation",
            CapabilityState.OBSERVED,
            "all required Gate 2 fields are exposed as structured span data",
        )
    missing = [
        f"{assessment.field}={assessment.state.value}"
        for assessment in trace.field_assessments()
        if assessment.state not in {FieldState.PRESENT, FieldState.TRANSFORMED}
    ]
    return CapabilityAssessment(
        "suitable for deterministic evaluation",
        CapabilityState.FAILED,
        "required fields missing or incomplete: " + ", ".join(missing),
    )


def relationship_capabilities(trace: Trace) -> tuple[CapabilityAssessment, CapabilityAssessment]:
    if trace.has_multiple_spans():
        multiple = CapabilityAssessment(
            "preserves multiple spans",
            CapabilityState.OBSERVED,
            f"retrieved {len(trace.spans)} span(s)",
        )
    else:
        multiple = CapabilityAssessment(
            "preserves multiple spans",
            CapabilityState.NOT_OBSERVED,
            f"retrieved {len(trace.spans)} span(s); root-only traces do not prove this",
        )

    if trace.has_valid_parent_child_relationship():
        parent_child = CapabilityAssessment(
            "preserves parent-child relationships",
            CapabilityState.OBSERVED,
            "at least one child parent_span_id resolved to a span_id in the same trace",
        )
    else:
        parent_child = CapabilityAssessment(
            "preserves parent-child relationships",
            CapabilityState.NOT_OBSERVED,
            "no validated root-child span pair was observed",
        )
    return multiple, parent_child
