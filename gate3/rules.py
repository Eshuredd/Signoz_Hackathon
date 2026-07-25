from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

try:
    from .models import EvaluationLevel, NormalizedTrace, RuleResult, RuleStatus, RunBundle, Severity, Span, is_valid_integer
except ImportError:  # pragma: no cover
    from models import EvaluationLevel, NormalizedTrace, RuleResult, RuleStatus, RunBundle, Severity, Span, is_valid_integer


ZERO_PARENT_SPAN_ID = "0" * 16
DOC = "gate3/spec/traceguard_telemetry_contract_v1.md"
RuleFunction = Callable[[NormalizedTrace | RunBundle], RuleResult]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    namespace: str
    evaluation_level: EvaluationLevel
    severity: Severity
    purpose: str
    applicability: str
    expected: Any
    function: RuleFunction
    supersedes: str | None = None
    deterministic: bool = True
    public: bool = True

    def evaluate(self, target: NormalizedTrace | RunBundle) -> RuleResult:
        return self.function(target)

    def result(
        self,
        status: RuleStatus,
        message: str,
        *,
        observed: Any,
        evidence: dict[str, Any] | None = None,
        affected_span_ids: tuple[str, ...] = (),
        affected_trace_ids: tuple[str, ...] = (),
    ) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            rule_name=self.name,
            namespace=self.namespace,
            severity=self.severity,
            status=status,
            message=message,
            expected=self.expected,
            observed=observed,
            evidence=evidence or {},
            affected_span_ids=tuple(sorted(x for x in affected_span_ids if x)),
            affected_trace_ids=tuple(sorted(x for x in affected_trace_ids if x)),
            deterministic=self.deterministic,
            documentation=f"{DOC}#{self.rule_id.lower()}",
        )

    def to_catalog_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.name,
            "namespace": self.namespace,
            "ruleset_version": "traceguard-telemetry-v2",
            "evaluation_level": self.evaluation_level.value,
            "severity": self.severity.value,
            "purpose": self.purpose,
            "applicability": self.applicability,
            "expected": self.expected,
            "deterministic": self.deterministic,
            "public": self.public,
            "supersedes": self.supersedes,
            "documentation_reference": f"{DOC}#{self.rule_id.lower()}",
            "expected_evidence": "See canonical specification evidence requirements.",
        }


def is_empty(value: object) -> bool:
    return not isinstance(value, str) or value.strip() == ""


def is_root_span(span: Span) -> bool:
    parent = span.parent_span_id
    return parent is None or parent == "" or parent.lower() == ZERO_PARENT_SPAN_ID


def root_spans(trace: NormalizedTrace) -> list[Span]:
    return [span for span in trace.spans if is_root_span(span)]


def agent_roots(trace: NormalizedTrace) -> list[Span]:
    return [span for span in root_spans(trace) if span.span_name == "agent.run"]


def single_agent_root(trace: NormalizedTrace) -> Span | None:
    roots = agent_roots(trace)
    return roots[0] if len(roots) == 1 and len(root_spans(trace)) == 1 else None


def is_tool_span(span: Span) -> bool:
    operation = span.attributes.get("gen_ai.operation.name")
    return span.span_name == "tool.call" or span.span_name.startswith("tool.") or operation == "execute_tool"


def is_model_operation(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in ("model", "chat", "completion", "llm", "generate"))


def is_model_span(span: Span) -> bool:
    return (
        span.span_name == "model.call"
        or span.span_name.startswith("model.")
        or "gen_ai.request.model" in span.attributes
        or is_model_operation(span.attributes.get("gen_ai.operation.name"))
    )


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def trace_ids_for_bundle(bundle: RunBundle) -> tuple[str, ...]:
    return tuple(sorted({trace.trace_id for trace in bundle.traces if trace.trace_id}))


def first_trace(target: NormalizedTrace | RunBundle) -> NormalizedTrace:
    if isinstance(target, NormalizedTrace):
        return target
    return target.traces[0] if target.traces else NormalizedTrace(1, "", (), None, "run-bundle", {})


def tg_tel_001(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-001"]
    trace = first_trace(target)
    roots = root_spans(trace)
    root_ids = tuple(sorted(span.span_id for span in roots if span.span_id))
    root_names = tuple(sorted(span.span_name for span in roots))
    passed = len(roots) == 1 and roots[0].span_name == "agent.run"
    return rule.result(
        RuleStatus.PASSED if passed else RuleStatus.FAILED,
        "Exactly one agent.run root span is present." if passed else "Trace must contain exactly one root span named agent.run.",
        observed={"root_span_count": len(roots), "root_span_ids": root_ids, "root_span_names": root_names},
        evidence={
            "root_span_count": len(roots),
            "root_span_ids": root_ids,
            "root_span_names": root_names,
            "expected_root_span_name": "agent.run",
        },
        affected_span_ids=root_ids,
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_002(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-002"]
    trace = first_trace(target)
    root = single_agent_root(trace)
    expected = ("agent.run_id", "agent.name", "agent.status")
    if root is None:
        return rule.result(
            RuleStatus.FAILED,
            "No unambiguous agent.run root span is available for required attribute checks.",
            observed={"root_span_id": None, "missing_attributes": list(expected)},
            evidence={"root_span_id": None, "missing_attributes": list(expected), "expected_attributes": list(expected)},
            affected_trace_ids=(trace.trace_id,),
        )
    missing = [key for key in expected if is_empty(root.attributes.get(key))]
    status = RuleStatus.PASSED if not missing else RuleStatus.FAILED
    return rule.result(
        status,
        "Agent root contains required identity and state attributes." if status == RuleStatus.PASSED else "Agent root is missing required attributes.",
        observed={"root_span_id": root.span_id, "missing_attributes": missing, "present_attribute_names": sorted(root.attributes)},
        evidence={
            "root_span_id": root.span_id,
            "missing_attributes": missing,
            "present_attribute_names": sorted(root.attributes),
            "expected_attributes": list(expected),
        },
        affected_span_ids=(root.span_id,),
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_003a(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-003A"]
    trace = first_trace(target)
    root = single_agent_root(trace)
    tools = sorted([span for span in trace.spans if is_tool_span(span)], key=lambda span: (span.span_id, span.index))
    if not tools:
        return rule.result(RuleStatus.NOT_APPLICABLE, "Trace contains no tool spans.", observed={"tool_span_ids": []}, evidence={"tool_span_ids": []}, affected_trace_ids=(trace.trace_id,))
    if root is None:
        return rule.result(
            RuleStatus.FAILED,
            "Tool parent chains cannot be proven without one unambiguous agent.run root.",
            observed={"tool_span_ids": [span.span_id for span in tools], "agent_run_root_span_id": None},
            evidence={"tool_span_ids": [span.span_id for span in tools], "expected_agent_run_root_span_id": None, "chain_termination_reason": "ambiguous_agent_root"},
            affected_span_ids=tuple(span.span_id for span in tools),
            affected_trace_ids=(trace.trace_id,),
        )
    spans_by_id = {span.span_id: span for span in trace.spans if span.span_id}
    failures: list[dict[str, Any]] = []
    for tool in tools:
        visited: list[str] = []
        parent_id = tool.parent_span_id
        seen = {tool.span_id}
        while True:
            if not parent_id or parent_id.lower() == ZERO_PARENT_SPAN_ID:
                failures.append({"tool_span_id": tool.span_id, "visited_parent_span_ids": visited, "chain_termination_reason": "terminated_before_agent_root"})
                break
            if parent_id in seen:
                failures.append({"tool_span_id": tool.span_id, "visited_parent_span_ids": visited, "cycle_path": [*visited, parent_id], "chain_termination_reason": "cycle"})
                break
            parent = spans_by_id.get(parent_id)
            if parent is None:
                failures.append({"tool_span_id": tool.span_id, "visited_parent_span_ids": visited, "unresolved_parent_id": parent_id, "chain_termination_reason": "missing_parent"})
                break
            visited.append(parent_id)
            if parent.span_id == root.span_id:
                break
            seen.add(parent_id)
            parent_id = parent.parent_span_id
    status = RuleStatus.PASSED if not failures else RuleStatus.FAILED
    return rule.result(
        status,
        "Every tool span parent chain reaches the agent.run root." if status == RuleStatus.PASSED else "One or more tool span parent chains do not reach the agent.run root.",
        observed={"tool_span_ids": [span.span_id for span in tools], "failures": failures},
        evidence={"tool_span_ids": [span.span_id for span in tools], "expected_agent_run_root_span_id": root.span_id, "failures": failures},
        affected_span_ids=tuple(item["tool_span_id"] for item in failures),
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_003b(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-003B"]
    if isinstance(target, NormalizedTrace):
        return rule.result(RuleStatus.NOT_APPLICABLE, "Run-level trace collection was not supplied.", observed={"trace_ids": [target.trace_id]}, evidence={"reason": "Run-level trace collection was not supplied."}, affected_trace_ids=(target.trace_id,))
    expected_run_id = target.agent_run_id
    matching_trace_ids: set[str] = set()
    matching_trace_count = 0
    foreign_trace_ids: set[str] = set()
    foreign_agent_run_ids: set[str] = set()
    traces_missing_run_id: list[str] = []
    traces_with_ambiguous_agent_root: list[str] = []
    for trace in target.traces:
        roots = agent_roots(trace)
        if len(roots) != 1 or len(root_spans(trace)) != 1:
            traces_with_ambiguous_agent_root.append(trace.trace_id)
            continue
        run_id = roots[0].attributes.get("agent.run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            traces_missing_run_id.append(trace.trace_id)
            continue
        if run_id == expected_run_id:
            matching_trace_count += 1
            if trace.trace_id:
                matching_trace_ids.add(trace.trace_id)
        else:
            if trace.trace_id:
                foreign_trace_ids.add(trace.trace_id)
            foreign_agent_run_ids.add(run_id)
    evidence = {
        "expected_agent_run_id": expected_run_id,
        "matching_trace_ids": tuple(sorted(matching_trace_ids)),
        "matching_trace_count": matching_trace_count,
        "foreign_trace_ids": tuple(sorted(foreign_trace_ids)),
        "foreign_agent_run_ids": tuple(sorted(foreign_agent_run_ids)),
        "traces_missing_run_id": tuple(sorted(traces_missing_run_id)),
        "traces_with_ambiguous_agent_root": tuple(sorted(traces_with_ambiguous_agent_root)),
        "expected_unique_trace_count": 1,
    }
    failures: list[str] = []
    if matching_trace_count == 0:
        failures.append("no_matching_run_trace")
    if len(matching_trace_ids) != 1:
        failures.append("matching_run_fragmented")
    if foreign_trace_ids:
        failures.append("foreign_run_trace_supplied")
    status = RuleStatus.PASSED if not failures else RuleStatus.FAILED
    return rule.result(
        status,
        "Agent run is contained in one trace." if status == RuleStatus.PASSED else "Run bundle traces do not all belong to one matching agent.run_id trace.",
        observed={**evidence, "failures": tuple(failures)},
        evidence={**evidence, "failures": tuple(failures)},
        affected_trace_ids=tuple(sorted((matching_trace_ids | foreign_trace_ids) if status == RuleStatus.FAILED else set())),
    )


def tg_tel_004(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-004"]
    trace = first_trace(target)
    tools = sorted([span for span in trace.spans if is_tool_span(span)], key=lambda span: (span.span_id, span.index))
    if not tools:
        return rule.result(RuleStatus.NOT_APPLICABLE, "Trace contains no tool spans.", observed={"tool_span_ids": []}, evidence={"tool_span_ids": []}, affected_trace_ids=(trace.trace_id,))
    missing = [span.span_id for span in tools if is_empty(span.attributes.get("tool.status"))]
    status_values = {span.span_id: span.attributes.get("tool.status") for span in tools}
    status = RuleStatus.PASSED if not missing else RuleStatus.FAILED
    return rule.result(
        status,
        "Every tool span contains tool.status." if status == RuleStatus.PASSED else "One or more tool spans are missing tool.status.",
        observed={"tool_span_ids": [span.span_id for span in tools], "spans_missing_tool_status": missing, "observed_status_values": status_values},
        evidence={"tool_span_ids": [span.span_id for span in tools], "spans_missing_tool_status": missing, "observed_status_values": status_values, "expected_non_empty_attribute": "tool.status"},
        affected_span_ids=tuple(missing),
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_005(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-005"]
    trace = first_trace(target)
    models = sorted([span for span in trace.spans if is_model_span(span)], key=lambda span: (span.span_id, span.index))
    if not models:
        return rule.result(RuleStatus.NOT_APPLICABLE, "Trace contains no model spans.", observed={"model_span_ids": []}, evidence={"model_span_ids": []}, affected_trace_ids=(trace.trace_id,))
    missing = [span.span_id for span in models if is_empty(span.attributes.get("gen_ai.request.model"))]
    values = {span.span_id: span.attributes.get("gen_ai.request.model") for span in models}
    status = RuleStatus.PASSED if not missing else RuleStatus.FAILED
    return rule.result(
        status,
        "Every model span identifies the requested model." if status == RuleStatus.PASSED else "One or more model spans are missing gen_ai.request.model.",
        observed={"model_span_ids": [span.span_id for span in models], "spans_missing_model_identity": missing, "observed_model_values": values},
        evidence={"model_span_ids": [span.span_id for span in models], "spans_missing_model_identity": missing, "observed_model_values": values},
        affected_span_ids=tuple(missing),
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_006(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-006"]
    trace = first_trace(target)
    models = sorted([span for span in trace.spans if is_model_span(span)], key=lambda span: (span.span_id, span.index))
    if not models:
        return rule.result(RuleStatus.NOT_APPLICABLE, "Trace contains no model spans.", observed={"model_span_ids": []}, evidence={"model_span_ids": []}, affected_trace_ids=(trace.trace_id,))
    fields = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")
    missing: dict[str, list[str]] = {}
    invalid: dict[str, dict[str, Any]] = {}
    values: dict[str, dict[str, Any]] = {}
    for span in models:
        values[span.span_id] = {field: span.attributes.get(field) for field in fields}
        for field in fields:
            value = span.attributes.get(field)
            if value is None:
                missing.setdefault(span.span_id, []).append(field)
            elif not is_valid_integer(value) or value < 0:
                invalid.setdefault(span.span_id, {})[field] = value
    status = RuleStatus.PASSED if not missing and not invalid else RuleStatus.FAILED
    return rule.result(
        status,
        "Every model span contains valid token usage." if status == RuleStatus.PASSED else "One or more model spans are missing or have invalid token usage.",
        observed={"model_span_ids": [span.span_id for span in models], "missing_token_fields": missing, "invalid_token_fields": invalid, "observed_values": values},
        evidence={"model_span_ids": [span.span_id for span in models], "missing_token_fields": missing, "invalid_token_fields": invalid, "observed_values": values, "expected_integer_constraints": "real integers, not booleans, zero or greater"},
        affected_span_ids=tuple(sorted(set(missing) | set(invalid))),
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_007(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-007"]
    trace = first_trace(target)
    affected: list[str] = []
    details: dict[str, dict[str, Any]] = {}
    for span in trace.spans:
        missing = [field for field in ("start_time", "end_time", "duration_nano") if span.get(field) is None]
        invalid: list[str] = []
        start = parse_time(span.get("start_time"))
        end = parse_time(span.get("end_time"))
        duration = span.get("duration_nano")
        if span.get("start_time") is not None and start is None:
            invalid.append("start_time")
        if span.get("end_time") is not None and end is None:
            invalid.append("end_time")
        if duration is not None and (not is_valid_integer(duration) or duration < 0):
            invalid.append("duration_nano")
        if start is not None and end is not None and end < start:
            invalid.append("end_time_before_start_time")
        if missing or invalid:
            affected.append(span.span_id)
            details[span.span_id or f"span_index_{span.index}"] = {
                "missing_fields": missing,
                "invalid_fields": invalid,
                "observed": {"start_time": span.get("start_time"), "end_time": span.get("end_time"), "duration_nano": duration},
            }
    status = RuleStatus.PASSED if not affected else RuleStatus.FAILED
    return rule.result(
        status,
        "All spans contain valid internally consistent timing." if status == RuleStatus.PASSED else "One or more spans have invalid timing.",
        observed={"affected_span_ids": affected, "details": details},
        evidence={"affected_span_ids": affected, "details": details},
        affected_span_ids=tuple(affected),
        affected_trace_ids=(trace.trace_id,),
    )


def tg_tel_008(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-TEL-008"]
    if isinstance(target, NormalizedTrace):
        return rule.result(RuleStatus.NOT_APPLICABLE, "Correlated log collection was not supplied.", observed={"log_count": 0}, evidence={"reason": "Correlated log collection was not supplied."}, affected_trace_ids=(target.trace_id,))
    if not target.logs:
        return rule.result(RuleStatus.NOT_APPLICABLE, "Run bundle contains no logs.", observed={"log_count": 0}, evidence={"log_count": 0, "expected_agent_run_id": target.agent_run_id}, affected_trace_ids=trace_ids_for_bundle(target))
    trace_ids = set(trace_ids_for_bundle(target))
    span_ids_by_trace = {trace.trace_id: {span.span_id for span in trace.spans if span.span_id} for trace in target.traces}
    bad: list[int] = []
    for log in target.logs:
        log_run_id = log.attributes.get("agent.run_id")
        if log_run_id != target.agent_run_id:
            bad.append(log.index)
            continue
        if log.trace_id and log.trace_id not in trace_ids:
            bad.append(log.index)
            continue
        if log.trace_id and log.span_id and log.span_id not in span_ids_by_trace.get(log.trace_id, set()):
            bad.append(log.index)
    status = RuleStatus.PASSED if not bad else RuleStatus.FAILED
    return rule.result(
        status,
        "All supplied logs correlate to the run bundle." if status == RuleStatus.PASSED else "One or more supplied logs do not correlate to the run bundle.",
        observed={"log_count": len(target.logs), "correlated_log_count": len(target.logs) - len(bad), "uncorrelated_log_indexes": bad, "observed_trace_ids": sorted(trace_ids)},
        evidence={"log_count": len(target.logs), "correlated_log_count": len(target.logs) - len(bad), "uncorrelated_log_indexes": bad, "expected_agent_run_id": target.agent_run_id, "observed_trace_ids": sorted(trace_ids)},
        affected_trace_ids=trace_ids_for_bundle(target),
    )


def tg_str_001(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-STR-001"]
    trace = first_trace(target)
    passed = len(trace.spans) > 0
    return rule.result(RuleStatus.PASSED if passed else RuleStatus.FAILED, "Trace contains at least one span." if passed else "Trace contains zero spans.", observed={"span_count": len(trace.spans)}, evidence={"observed_span_count": len(trace.spans)}, affected_trace_ids=(trace.trace_id,))


def tg_str_002(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-STR-002"]
    trace = first_trace(target)
    affected: list[str] = []
    missing_by_span: dict[str, list[str]] = {}
    for span in trace.spans:
        missing = [field for field in ("trace_id", "span_id", "span_name") if is_empty(span.get(field))]
        if missing:
            affected.append(span.span_id)
            missing_by_span[span.span_id or f"span_index_{span.index}"] = missing
    return rule.result(RuleStatus.PASSED if not affected else RuleStatus.FAILED, "All spans contain required identity." if not affected else "One or more spans are missing required identity.", observed={"affected_span_ids": affected, "missing_fields": missing_by_span}, evidence={"affected_span_ids": affected, "missing_fields": missing_by_span, "expected_fields": ["trace_id", "span_id", "span_name"]}, affected_span_ids=tuple(affected), affected_trace_ids=(trace.trace_id,))


def tg_str_003(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-STR-003"]
    trace = first_trace(target)
    observed = tuple(sorted({span.trace_id for span in trace.spans if span.trace_id}))
    mismatches = tuple(sorted(span.span_id for span in trace.spans if span.trace_id and span.trace_id != trace.trace_id))
    return rule.result(RuleStatus.PASSED if not mismatches and len(observed) <= 1 else RuleStatus.FAILED, "Span trace IDs match the enclosing trace." if not mismatches and len(observed) <= 1 else "Span trace IDs are inconsistent with the enclosing trace.", observed={"enclosing_trace_id": trace.trace_id, "observed_trace_ids": observed}, evidence={"enclosing_trace_id": trace.trace_id, "observed_trace_ids": observed, "mismatched_span_ids": mismatches}, affected_span_ids=mismatches, affected_trace_ids=(trace.trace_id,))


def tg_str_004(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-STR-004"]
    trace = first_trace(target)
    counts: dict[str, int] = {}
    for span in trace.spans:
        if span.span_id:
            counts[span.span_id] = counts.get(span.span_id, 0) + 1
    dupes = tuple(sorted(span_id for span_id, count in counts.items() if count > 1))
    return rule.result(RuleStatus.PASSED if not dupes else RuleStatus.FAILED, "Span IDs are unique within the trace." if not dupes else "Duplicate span IDs were found within the trace.", observed={"duplicate_span_ids": dupes}, evidence={"duplicate_span_ids": dupes, "span_id_counts": {key: counts[key] for key in dupes}}, affected_span_ids=dupes, affected_trace_ids=(trace.trace_id,))


def tg_str_005(target: NormalizedTrace | RunBundle) -> RuleResult:
    rule = RULE_BY_ID["TG-STR-005"]
    trace = first_trace(target)
    missing = tuple(sorted(span.span_id for span in trace.spans if is_empty(span.get("service_name")) and is_empty(span.resource_attributes.get("service.name"))))
    return rule.result(RuleStatus.PASSED if not missing else RuleStatus.FAILED, "Every span exposes service identity." if not missing else "One or more spans are missing service identity.", observed={"spans_missing_service_identity": missing}, evidence={"spans_missing_service_identity": missing, "expected": ["service_name", "resource_attributes.service.name"]}, affected_span_ids=missing, affected_trace_ids=(trace.trace_id,))


RULES = (
    Rule("TG-TEL-001", "AGENT_RUN_ROOT", "TG-TEL", EvaluationLevel.TRACE, Severity.BLOCKING, "Require exactly one root span representing the agent execution.", "trace and run bundles", {"root_span_name": "agent.run", "root_count": 1}, tg_tel_001, "Old TG-TEL-005"),
    Rule("TG-TEL-002", "AGENT_RUN_REQUIRED_ATTRIBUTES", "TG-TEL", EvaluationLevel.TRACE, Severity.BLOCKING, "Require canonical agent identity and state attributes.", "trace and run bundles with agent.run root", {"required_root_attributes": ["agent.run_id", "agent.name", "agent.status"]}, tg_tel_002, "Old TG-TEL-010"),
    Rule("TG-TEL-003A", "TOOL_PARENT_CHAIN", "TG-TEL", EvaluationLevel.TRACE, Severity.BLOCKING, "Require every tool span to resolve to the agent.run root.", "tool spans only", {"parent_chain_terminates_at": "agent.run root"}, tg_tel_003a, "Old TG-TEL-006"),
    Rule("TG-TEL-003B", "NO_TRACE_FRAGMENTATION", "TG-TEL", EvaluationLevel.RUN, Severity.BLOCKING, "Require one agent execution identified by agent.run_id to remain within one trace.", "run bundles only", {"maximum_trace_count": 1}, tg_tel_003b),
    Rule("TG-TEL-004", "TOOL_STATUS", "TG-TEL", EvaluationLevel.TRACE, Severity.BLOCKING, "Require every tool span to contain tool.status.", "tool spans only", {"required_tool_attribute": "tool.status"}, tg_tel_004),
    Rule("TG-TEL-005", "MODEL_IDENTITY", "TG-TEL", EvaluationLevel.TRACE, Severity.BLOCKING, "Require every model invocation span to identify the requested model.", "model spans only", {"required_model_attribute": "gen_ai.request.model"}, tg_tel_005),
    Rule("TG-TEL-006", "TOKEN_USAGE", "TG-TEL", EvaluationLevel.TRACE, Severity.WARNING, "Require model invocation spans to expose token usage.", "model spans only", {"required_token_attributes": ["gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"], "integer_constraints": "not booleans, zero or greater"}, tg_tel_006),
    Rule("TG-TEL-007", "TIMESTAMP_VALIDITY", "TG-TEL", EvaluationLevel.TRACE, Severity.BLOCKING, "Require valid and internally consistent span timing.", "all spans", {"required_fields": ["start_time", "end_time", "duration_nano"]}, tg_tel_007, "Old TG-TEL-007 and TG-TEL-008"),
    Rule("TG-TEL-008", "LOG_CORRELATION", "TG-TEL", EvaluationLevel.RUN, Severity.WARNING, "Verify that logs associated with an agent execution can be correlated back to the correct run and trace.", "run bundles with logs", {"log_attributes": ["agent.run_id", "trace_id"]}, tg_tel_008),
    Rule("TG-STR-001", "TRACE_HAS_SPANS", "TG-STR", EvaluationLevel.TRACE, Severity.BLOCKING, "Require at least one span.", "all traces", {"minimum_span_count": 1}, tg_str_001, "Old TG-TEL-001"),
    Rule("TG-STR-002", "REQUIRED_SPAN_IDENTITY", "TG-STR", EvaluationLevel.TRACE, Severity.BLOCKING, "Require non-empty trace_id, span_id, and span_name.", "all spans", {"required_fields": ["trace_id", "span_id", "span_name"]}, tg_str_002, "Old TG-TEL-002"),
    Rule("TG-STR-003", "TRACE_ID_CONSISTENCY", "TG-STR", EvaluationLevel.TRACE, Severity.BLOCKING, "Require every span in one trace object to match the enclosing trace ID.", "all traces", {"all_span_trace_ids_match_enclosing_trace_id": True}, tg_str_003, "Old TG-TEL-003"),
    Rule("TG-STR-004", "UNIQUE_SPAN_IDS", "TG-STR", EvaluationLevel.TRACE, Severity.BLOCKING, "Require unique non-empty span IDs inside one trace.", "all traces", {"span_ids_unique": True}, tg_str_004, "Old TG-TEL-004"),
    Rule("TG-STR-005", "SERVICE_IDENTITY", "TG-STR", EvaluationLevel.TRACE, Severity.WARNING, "Require service identity through service_name or resource service.name.", "all spans", {"service_identity_fields": ["service_name", "resource_attributes.service.name"]}, tg_str_005, "Old TG-TEL-009"),
)

RULE_BY_ID = {rule.rule_id: rule for rule in RULES}
