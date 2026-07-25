from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


EVALUATOR_VERSION = "0.2.0"
RULESET_VERSION = "traceguard-telemetry-v2"
SUPPORTED_TRACE_INPUT_SCHEMA_VERSION = 1
SUPPORTED_EXPECTATION_SCHEMA_VERSION = 1
SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION = 1


class Verdict(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    BLOCK = "BLOCK"

    @property
    def label(self) -> str:
        if self == Verdict.PASS_WITH_WARNINGS:
            return "PASS WITH WARNINGS"
        return self.value


class RuleStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    EVALUATION_ERROR = "EVALUATION_ERROR"


class Severity(str, Enum):
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class EvaluationLevel(str, Enum):
    TRACE = "trace"
    RUN = "run"


@dataclass(frozen=True)
class Span:
    raw: dict[str, Any]
    index: int

    def get(self, key: str) -> Any:
        return self.raw.get(key)

    @property
    def span_id(self) -> str:
        value = self.raw.get("span_id")
        return value if isinstance(value, str) else ""

    @property
    def trace_id(self) -> str:
        value = self.raw.get("trace_id")
        return value if isinstance(value, str) else ""

    @property
    def span_name(self) -> str:
        value = self.raw.get("span_name")
        return value if isinstance(value, str) else ""

    @property
    def parent_span_id(self) -> str | None:
        value = self.raw.get("parent_span_id")
        return value if value is None or isinstance(value, str) else None

    @property
    def attributes(self) -> dict[str, Any]:
        value = self.raw.get("attributes")
        return value if isinstance(value, dict) else {}

    @property
    def resource_attributes(self) -> dict[str, Any]:
        value = self.raw.get("resource_attributes")
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class NormalizedTrace:
    schema_version: int
    trace_id: str
    spans: tuple[Span, ...]
    retrieved_at: str | None
    source: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LogRecord:
    index: int
    timestamp: str | None
    trace_id: str | None
    span_id: str | None
    attributes: dict[str, Any]
    body: Any


@dataclass(frozen=True)
class RunBundle:
    schema_version: int
    agent_run_id: str
    traces: tuple[NormalizedTrace, ...]
    logs: tuple[LogRecord, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    rule_name: str
    namespace: str
    severity: Severity
    status: RuleStatus
    message: str
    expected: Any
    observed: Any
    evidence: dict[str, Any]
    affected_span_ids: tuple[str, ...] = ()
    affected_trace_ids: tuple[str, ...] = ()
    deterministic: bool = True
    documentation: str = ""

    def sort_key(self) -> tuple[str, str]:
        return (self.namespace, self.rule_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "namespace": self.namespace,
            "severity": self.severity.value,
            "status": self.status.value,
            "message": self.message,
            "expected": stable_value(self.expected),
            "observed": stable_value(self.observed),
            "evidence": stable_value(self.evidence),
            "affected_span_ids": list(self.affected_span_ids),
            "affected_trace_ids": list(self.affected_trace_ids),
            "deterministic": self.deterministic,
            "documentation": self.documentation,
        }


@dataclass(frozen=True)
class EvaluationSummary:
    total_rule_count: int
    passed_count: int
    failed_count: int
    not_applicable_count: int
    evaluation_error_count: int
    blocking_failure_count: int
    warning_failure_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total_rule_count": self.total_rule_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "not_applicable_count": self.not_applicable_count,
            "evaluation_error_count": self.evaluation_error_count,
            "blocking_failure_count": self.blocking_failure_count,
            "warning_failure_count": self.warning_failure_count,
        }


@dataclass(frozen=True)
class EvaluationResult:
    evaluator_version: str
    ruleset_version: str
    evaluated_at: datetime
    evaluation_level: EvaluationLevel
    agent_run_id: str | None
    trace_ids: tuple[str, ...]
    verdict: Verdict
    rule_results: tuple[RuleResult, ...]
    summary: EvaluationSummary
    source: str | None
    input_schema_version: int

    def to_dict(self, *, include_evaluated_at: bool = True) -> dict[str, Any]:
        result = {
            "evaluator_version": self.evaluator_version,
            "ruleset_version": self.ruleset_version,
            "evaluation_level": self.evaluation_level.value,
            "agent_run_id": self.agent_run_id,
            "trace_ids": list(self.trace_ids),
            "verdict": self.verdict.value,
            "verdict_label": self.verdict.label,
            "rule_results": [item.to_dict() for item in sorted(self.rule_results, key=lambda rule: rule.sort_key())],
            "summary": self.summary.to_dict(),
            "source": self.source,
            "input_schema_version": self.input_schema_version,
        }
        if include_evaluated_at:
            result["evaluated_at"] = self.evaluated_at.isoformat().replace("+00:00", "Z")
        return result

    def to_json(self, *, include_evaluated_at: bool = True) -> str:
        return json.dumps(
            self.to_dict(include_evaluated_at=include_evaluated_at),
            sort_keys=True,
            separators=(",", ":"),
        )


def now_utc() -> datetime:
    return datetime.now(UTC)


def stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: stable_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [stable_value(item) for item in value]
    if isinstance(value, set):
        return [stable_value(item) for item in sorted(value)]
    if isinstance(value, Enum):
        return value.value
    return value


def is_valid_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def verdict_from_rule_results(rule_results: list[RuleResult] | tuple[RuleResult, ...]) -> Verdict:
    if any(item.status == RuleStatus.EVALUATION_ERROR for item in rule_results):
        return Verdict.BLOCK
    if any(item.status == RuleStatus.FAILED and item.severity == Severity.BLOCKING for item in rule_results):
        return Verdict.BLOCK
    if any(item.status == RuleStatus.FAILED and item.severity == Severity.WARNING for item in rule_results):
        return Verdict.PASS_WITH_WARNINGS
    return Verdict.PASS


def summary_from_rule_results(rule_results: list[RuleResult] | tuple[RuleResult, ...]) -> EvaluationSummary:
    return EvaluationSummary(
        total_rule_count=len(rule_results),
        passed_count=sum(1 for item in rule_results if item.status == RuleStatus.PASSED),
        failed_count=sum(1 for item in rule_results if item.status == RuleStatus.FAILED),
        not_applicable_count=sum(1 for item in rule_results if item.status == RuleStatus.NOT_APPLICABLE),
        evaluation_error_count=sum(1 for item in rule_results if item.status == RuleStatus.EVALUATION_ERROR),
        blocking_failure_count=sum(
            1 for item in rule_results if item.status == RuleStatus.FAILED and item.severity == Severity.BLOCKING
        ),
        warning_failure_count=sum(
            1 for item in rule_results if item.status == RuleStatus.FAILED and item.severity == Severity.WARNING
        ),
    )


def equivalent_results(left: EvaluationResult, right: EvaluationResult) -> bool:
    return left.to_dict(include_evaluated_at=False) == right.to_dict(include_evaluated_at=False)
