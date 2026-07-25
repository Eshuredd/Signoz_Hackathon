from __future__ import annotations

from dataclasses import dataclass

from gate2.models import Trace

from .exporter import EmissionResult
from .scenarios import PreflightScenario


@dataclass(frozen=True)
class PreservationVerificationResult:
    trace_id_match: bool
    span_count_match: bool
    span_names_match: bool
    span_ids_match: bool
    parent_relationships_match: bool
    preflight_correlation_match: bool
    required_attributes_preserved: bool
    intentional_absences_preserved: bool
    service_identity_preserved: bool
    timing_preserved: bool
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id_match": self.trace_id_match,
            "span_count_match": self.span_count_match,
            "span_names_match": self.span_names_match,
            "span_ids_match": self.span_ids_match,
            "parent_relationships_match": self.parent_relationships_match,
            "preflight_correlation_match": self.preflight_correlation_match,
            "required_attributes_preserved": self.required_attributes_preserved,
            "intentional_absences_preserved": self.intentional_absences_preserved,
            "service_identity_preserved": self.service_identity_preserved,
            "timing_preserved": self.timing_preserved,
            "errors": list(self.errors),
            "passed": self.passed,
        }


def verify_retrieved_trace(
    *,
    scenario: PreflightScenario,
    emission: EmissionResult,
    trace: Trace,
) -> PreservationVerificationResult:
    errors: list[str] = []
    spans_by_name = {span.span_name: span for span in trace.spans}
    names = set(spans_by_name)
    expected_names = {"agent.run", "tool.call", "model.call"}

    trace_id_match = trace.trace_id == emission.trace_id and all(span.trace_id == emission.trace_id for span in trace.spans)
    _record(errors, trace_id_match, "retrieved trace ID or span trace IDs did not match emitted trace ID")

    span_count_match = len(trace.spans) == 3
    _record(errors, span_count_match, "retrieved trace did not contain exactly three spans")

    span_names_match = names == expected_names and len(spans_by_name) == len(trace.spans)
    _record(errors, span_names_match, "retrieved span names were not exactly agent.run, tool.call, model.call")

    span_ids_match = span_names_match and all(spans_by_name[name].span_id == emission.span_ids_by_name[name] for name in expected_names)
    _record(errors, span_ids_match, "retrieved span IDs did not match emitted span IDs")

    parent_relationships_match = False
    if span_names_match:
        root_id = spans_by_name["agent.run"].span_id
        parent_relationships_match = (
            spans_by_name["agent.run"].parent_span_id in {None, "", "0" * 16}
            and spans_by_name["tool.call"].parent_span_id == root_id
            and spans_by_name["model.call"].parent_span_id == root_id
        )
    _record(errors, parent_relationships_match, "retrieved parent relationships did not match emitted root-child structure")

    preflight_correlation_match = span_names_match and all(
        spans_by_name[name].attributes.get("traceguard.preflight_id") == scenario.preflight_id
        for name in expected_names
    )
    _record(errors, preflight_correlation_match, "retrieved spans did not preserve traceguard.preflight_id")

    required_attributes_preserved = span_names_match and all(
        spans_by_name[name].attributes.get(key) == value
        for name, attrs in emission.expected_attributes_by_name.items()
        for key, value in attrs.items()
    )
    _record(errors, required_attributes_preserved, "retrieved spans did not preserve emitted required attributes")

    intentional_absences_preserved = span_names_match and all(
        _is_absent(spans_by_name[name].attributes.get(key))
        for name, absent_keys in emission.intentionally_absent_attributes_by_name.items()
        for key in absent_keys
    )
    _record(errors, intentional_absences_preserved, "retrieved spans injected an intentionally absent attribute")

    service_identity_preserved = all(
        bool(span.service_name) or bool(span.resource_attributes.get("service.name"))
        for span in trace.spans
    )
    _record(errors, service_identity_preserved, "retrieved spans did not preserve service identity")

    timing_preserved = all(
        span.start_time is not None and span.end_time is not None and span.duration_nano is not None
        for span in trace.spans
    )
    _record(errors, timing_preserved, "retrieved spans did not preserve start_time, end_time, and duration_nano")

    return PreservationVerificationResult(
        trace_id_match=trace_id_match,
        span_count_match=span_count_match,
        span_names_match=span_names_match,
        span_ids_match=span_ids_match,
        parent_relationships_match=parent_relationships_match,
        preflight_correlation_match=preflight_correlation_match,
        required_attributes_preserved=required_attributes_preserved,
        intentional_absences_preserved=intentional_absences_preserved,
        service_identity_preserved=service_identity_preserved,
        timing_preserved=timing_preserved,
        errors=tuple(errors),
    )


def _record(errors: list[str], passed: bool, message: str) -> None:
    if not passed:
        errors.append(message)


def _is_absent(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")
