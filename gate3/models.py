from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


EVALUATOR_VERSION = "0.1.0"
RULESET_VERSION = "tg-tel-v1"


class Verdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class Severity(str, Enum):
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


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
class RuleFinding:
    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    evidence: dict[str, Any]
    span_ids: tuple[str, ...] = ()
    deterministic: bool = True
    documentation: str = ""

    def sort_key(self) -> tuple[str, str, str]:
        span_key = self.span_ids[0] if self.span_ids else ""
        return (self.rule_id, span_key, self.message)

    def to_dict(self) -> dict[str, Any]:
        item = {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": stable_value(self.evidence),
            "span_ids": list(self.span_ids),
            "deterministic": self.deterministic,
        }
        if self.documentation:
            item["documentation"] = self.documentation
        return item


@dataclass(frozen=True)
class EvaluationSummary:
    blocking_count: int
    warning_count: int
    evaluated_rule_count: int
    passed_rule_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "evaluated_rule_count": self.evaluated_rule_count,
            "passed_rule_count": self.passed_rule_count,
        }


@dataclass(frozen=True)
class EvaluationResult:
    evaluator_version: str
    ruleset_version: str
    evaluated_at: datetime
    trace_id: str
    verdict: Verdict
    findings: tuple[RuleFinding, ...]
    summary: EvaluationSummary
    source: str | None
    input_schema_version: int

    def to_dict(self, *, include_evaluated_at: bool = True) -> dict[str, Any]:
        result = {
            "evaluator_version": self.evaluator_version,
            "ruleset_version": self.ruleset_version,
            "trace_id": self.trace_id,
            "verdict": self.verdict.value,
            "findings": [finding.to_dict() for finding in sorted(self.findings, key=lambda item: item.sort_key())],
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
    return value


def verdict_from_findings(findings: list[RuleFinding] | tuple[RuleFinding, ...]) -> Verdict:
    if any(finding.severity == Severity.BLOCKING for finding in findings):
        return Verdict.BLOCK
    if any(finding.severity == Severity.WARNING for finding in findings):
        return Verdict.WARN
    return Verdict.PASS


def summary_from_findings(
    findings: list[RuleFinding] | tuple[RuleFinding, ...],
    *,
    evaluated_rule_count: int,
) -> EvaluationSummary:
    blocking_count = sum(1 for finding in findings if finding.severity == Severity.BLOCKING)
    warning_count = sum(1 for finding in findings if finding.severity == Severity.WARNING)
    triggered_rule_ids = {finding.rule_id for finding in findings}
    return EvaluationSummary(
        blocking_count=blocking_count,
        warning_count=warning_count,
        evaluated_rule_count=evaluated_rule_count,
        passed_rule_count=evaluated_rule_count - len(triggered_rule_ids),
    )


def equivalent_results(left: EvaluationResult, right: EvaluationResult) -> bool:
    return left.to_dict(include_evaluated_at=False) == right.to_dict(include_evaluated_at=False)
