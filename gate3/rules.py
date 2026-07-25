from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

try:
    from .models import NormalizedTrace, RuleFinding, Severity, Span
except ImportError:  # pragma: no cover
    from models import NormalizedTrace, RuleFinding, Severity, Span


RuleFunction = Callable[[NormalizedTrace], list[RuleFinding]]
ZERO_PARENT_SPAN_ID = "0" * 16


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    severity: Severity
    purpose: str
    scope: str
    function: RuleFunction
    deterministic: bool = True

    def evaluate(self, trace: NormalizedTrace) -> list[RuleFinding]:
        return self.function(trace)

    def to_catalog_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "severity": self.severity.value,
            "purpose": self.purpose,
            "scope": self.scope,
            "deterministic": self.deterministic,
        }


def finding(
    rule: Rule,
    message: str,
    evidence: dict[str, object],
    *,
    span_ids: tuple[str, ...] = (),
) -> RuleFinding:
    return RuleFinding(
        rule_id=rule.rule_id,
        rule_name=rule.name,
        severity=rule.severity,
        message=message,
        evidence=evidence,
        span_ids=span_ids,
        deterministic=rule.deterministic,
        documentation=rule.purpose,
    )


def is_empty_string(value: object) -> bool:
    return not isinstance(value, str) or value.strip() == ""


def is_root_span(span: Span) -> bool:
    parent = span.parent_span_id
    return parent is None or parent == "" or parent.lower() == ZERO_PARENT_SPAN_ID


def root_spans(trace: NormalizedTrace) -> list[Span]:
    return [span for span in trace.spans if is_root_span(span)]


def single_root(trace: NormalizedTrace) -> Span | None:
    roots = root_spans(trace)
    return roots[0] if len(roots) == 1 else None


def span_ref(span: Span) -> tuple[str, ...]:
    return (span.span_id,) if span.span_id else ()


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def tg_tel_001(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-001"]
    if len(trace.spans) == 0:
        return [finding(rule, "Trace contains zero spans.", {"observed_span_count": 0})]
    return []


def tg_tel_002(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-002"]
    findings: list[RuleFinding] = []
    for span in trace.spans:
        missing = [field for field in ("trace_id", "span_id", "span_name") if is_empty_string(span.get(field))]
        if missing:
            findings.append(
                finding(
                    rule,
                    "Span is missing required identity fields.",
                    {"missing_fields": missing, "span_index": span.index},
                    span_ids=span_ref(span),
                )
            )
    return findings


def tg_tel_003(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-003"]
    observed = sorted({span.trace_id for span in trace.spans if span.trace_id})
    mismatch = any(span.trace_id and span.trace_id != trace.trace_id for span in trace.spans)
    if mismatch or len(observed) > 1:
        return [
            finding(
                rule,
                "Span trace IDs are inconsistent with the enclosing trace.",
                {"enclosing_trace_id": trace.trace_id, "observed_trace_ids": observed},
            )
        ]
    return []


def tg_tel_004(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-004"]
    counts: dict[str, int] = {}
    for span in trace.spans:
        if span.span_id:
            counts[span.span_id] = counts.get(span.span_id, 0) + 1
    return [
        finding(
            rule,
            "Duplicate span_id found in trace.",
            {"duplicate_span_id": span_id, "occurrence_count": count},
            span_ids=(span_id,),
        )
        for span_id, count in sorted(counts.items())
        if count > 1
    ]


def tg_tel_005(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-005"]
    if not trace.spans:
        return []
    roots = root_spans(trace)
    if len(roots) != 1:
        return [
            finding(
                rule,
                "Trace must contain exactly one root span.",
                {"observed_root_count": len(roots), "root_span_ids": sorted(span.span_id for span in roots if span.span_id)},
            )
        ]
    return []


def tg_tel_006(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-006"]
    span_ids = {span.span_id for span in trace.spans if span.span_id}
    findings: list[RuleFinding] = []
    for span in trace.spans:
        parent = span.parent_span_id
        if is_root_span(span):
            continue
        if not parent or parent not in span_ids:
            findings.append(
                finding(
                    rule,
                    "Non-root span references an unresolved parent_span_id.",
                    {"child_span_id": span.span_id, "unresolved_parent_span_id": parent},
                    span_ids=span_ref(span),
                )
            )
    return findings


def tg_tel_007(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-007"]
    findings: list[RuleFinding] = []
    for span in trace.spans:
        missing = [field for field in ("start_time", "end_time", "duration_nano") if span.get(field) is None]
        if missing:
            findings.append(
                finding(
                    rule,
                    "Span is missing required timing fields.",
                    {"missing_timing_fields": missing},
                    span_ids=span_ref(span),
                )
            )
    return findings


def tg_tel_008(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-008"]
    findings: list[RuleFinding] = []
    for span in trace.spans:
        duration = span.get("duration_nano")
        if isinstance(duration, int) and duration < 0:
            findings.append(
                finding(
                    rule,
                    "Span duration_nano is negative.",
                    {"duration_nano": duration},
                    span_ids=span_ref(span),
                )
            )
        start = parse_time(span.get("start_time"))
        end = parse_time(span.get("end_time"))
        if start is not None and end is not None and end < start:
            findings.append(
                finding(
                    rule,
                    "Span end_time is before start_time.",
                    {"start_time": span.get("start_time"), "end_time": span.get("end_time")},
                    span_ids=span_ref(span),
                )
            )
    return findings


def tg_tel_009(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-009"]
    findings: list[RuleFinding] = []
    for span in trace.spans:
        service_name_present = isinstance(span.get("service_name"), str) and span.get("service_name").strip() != ""
        resource_service_name_present = (
            isinstance(span.resource_attributes.get("service.name"), str)
            and span.resource_attributes.get("service.name").strip() != ""
        )
        if not service_name_present and not resource_service_name_present:
            findings.append(
                finding(
                    rule,
                    "Span is missing service identity.",
                    {
                        "span_id": span.span_id,
                        "service_name_present": False,
                        "resource_service_name_present": False,
                    },
                    span_ids=span_ref(span),
                )
            )
    return findings


def tg_tel_010(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-010"]
    root = single_root(trace)
    if root is None:
        return []
    if is_empty_string(root.attributes.get("agent.run_id")):
        return [
            finding(
                rule,
                "Root span is missing agent.run_id.",
                {"root_span_id": root.span_id, "missing_attribute": "agent.run_id"},
                span_ids=span_ref(root),
            )
        ]
    return []


def tg_tel_011(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-011"]
    root = single_root(trace)
    if root is None:
        return []
    if is_empty_string(root.attributes.get("traceguard.run_id")):
        return [
            finding(
                rule,
                "Root span is missing traceguard.run_id.",
                {"root_span_id": root.span_id, "missing_attribute": "traceguard.run_id"},
                span_ids=span_ref(root),
            )
        ]
    return []


def tg_tel_012(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-012"]
    root = single_root(trace)
    if root is None:
        return []
    root_agent = root.attributes.get("agent.run_id")
    root_traceguard = root.attributes.get("traceguard.run_id")
    findings: list[RuleFinding] = []
    if not is_empty_string(root_agent) and not is_empty_string(root_traceguard) and root_agent != root_traceguard:
        findings.append(
            finding(
                rule,
                "Root run ID attributes contradict each other.",
                {
                    "affected_span_id": root.span_id,
                    "attribute_names": ["agent.run_id", "traceguard.run_id"],
                    "conflicting_values": {"agent.run_id": root_agent, "traceguard.run_id": root_traceguard},
                },
                span_ids=span_ref(root),
            )
        )
    for span in trace.spans:
        agent = span.attributes.get("agent.run_id")
        traceguard = span.attributes.get("traceguard.run_id")
        if not is_empty_string(agent) and not is_empty_string(traceguard) and agent != traceguard:
            if span.span_id != root.span_id:
                findings.append(
                    finding(
                        rule,
                        "Span run ID attributes contradict each other.",
                        {
                            "affected_span_id": span.span_id,
                            "attribute_names": ["agent.run_id", "traceguard.run_id"],
                            "conflicting_values": {"agent.run_id": agent, "traceguard.run_id": traceguard},
                        },
                        span_ids=span_ref(span),
                    )
                )
        if span.span_id == root.span_id:
            continue
        if not is_empty_string(agent) and not is_empty_string(root_agent) and agent != root_agent:
            findings.append(
                finding(
                    rule,
                    "Child agent.run_id contradicts the root agent.run_id.",
                    {
                        "affected_span_id": span.span_id,
                        "attribute_names": ["agent.run_id"],
                        "conflicting_values": {"root.agent.run_id": root_agent, "child.agent.run_id": agent},
                    },
                    span_ids=span_ref(span),
                )
            )
        if not is_empty_string(traceguard) and not is_empty_string(root_traceguard) and traceguard != root_traceguard:
            findings.append(
                finding(
                    rule,
                    "Child traceguard.run_id contradicts the root traceguard.run_id.",
                    {
                        "affected_span_id": span.span_id,
                        "attribute_names": ["traceguard.run_id"],
                        "conflicting_values": {
                            "root.traceguard.run_id": root_traceguard,
                            "child.traceguard.run_id": traceguard,
                        },
                    },
                    span_ids=span_ref(span),
                )
            )
    return findings


def tg_tel_013(trace: NormalizedTrace) -> list[RuleFinding]:
    rule = RULE_BY_ID["TG-TEL-013"]
    root = single_root(trace)
    if root is None:
        return []
    missing = [
        field
        for field in ("traceguard.project", "traceguard.gate")
        if is_empty_string(root.attributes.get(field))
    ]
    if missing:
        return [
            finding(
                rule,
                "Root span is missing TraceGuard context attributes.",
                {"root_span_id": root.span_id, "missing_context_attributes": missing},
                span_ids=span_ref(root),
            )
        ]
    return []


RULES = (
    Rule("TG-TEL-001", "TRACE_HAS_SPANS", Severity.BLOCKING, "Require at least one span in the normalized trace.", "trace", tg_tel_001),
    Rule("TG-TEL-002", "REQUIRED_SPAN_IDENTITY", Severity.BLOCKING, "Require trace_id, span_id, and span_name on every span.", "span", tg_tel_002),
    Rule("TG-TEL-003", "TRACE_ID_CONSISTENCY", Severity.BLOCKING, "Require span trace IDs to match the enclosing trace ID.", "trace", tg_tel_003),
    Rule("TG-TEL-004", "UNIQUE_SPAN_IDS", Severity.BLOCKING, "Require unique non-empty span IDs within one trace.", "trace", tg_tel_004),
    Rule("TG-TEL-005", "SINGLE_ROOT_SPAN", Severity.BLOCKING, "Require exactly one root span when spans exist.", "trace", tg_tel_005),
    Rule("TG-TEL-006", "PARENT_REFERENCE_INTEGRITY", Severity.BLOCKING, "Require non-root parent_span_id values to resolve within the trace.", "span", tg_tel_006),
    Rule("TG-TEL-007", "REQUIRED_TIMING_FIELDS", Severity.BLOCKING, "Require start_time, end_time, and duration_nano on every span.", "span", tg_tel_007),
    Rule("TG-TEL-008", "VALID_TIMING_ORDER", Severity.BLOCKING, "Reject negative durations and end_time values before start_time.", "span", tg_tel_008),
    Rule("TG-TEL-009", "SERVICE_IDENTITY", Severity.WARNING, "Require service identity from service_name or resource service.name.", "span", tg_tel_009),
    Rule("TG-TEL-010", "AGENT_RUN_CORRELATION", Severity.BLOCKING, "Require root agent.run_id for external run correlation.", "root", tg_tel_010),
    Rule("TG-TEL-011", "TRACEGUARD_RUN_CORRELATION", Severity.WARNING, "Warn when root traceguard.run_id is absent.", "root", tg_tel_011),
    Rule("TG-TEL-012", "RUN_ID_CONSISTENCY", Severity.BLOCKING, "Require run ID attributes to be internally consistent.", "trace", tg_tel_012),
    Rule("TG-TEL-013", "TRACEGUARD_CONTEXT", Severity.WARNING, "Warn when root TraceGuard project or gate context is absent.", "root", tg_tel_013),
)

RULE_BY_ID = {rule.rule_id: rule for rule in RULES}
