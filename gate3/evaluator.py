from __future__ import annotations

try:
    from .models import (
        EVALUATOR_VERSION,
        RULESET_VERSION,
        EvaluationResult,
        NormalizedTrace,
        RuleFinding,
        now_utc,
        summary_from_findings,
        verdict_from_findings,
    )
    from .rules import RULES
except ImportError:  # pragma: no cover
    from models import (
        EVALUATOR_VERSION,
        RULESET_VERSION,
        EvaluationResult,
        NormalizedTrace,
        RuleFinding,
        now_utc,
        summary_from_findings,
        verdict_from_findings,
    )
    from rules import RULES


class EvaluatorInternalError(Exception):
    """Raised for unexpected evaluator defects."""


def evaluate_trace(trace: NormalizedTrace) -> EvaluationResult:
    try:
        findings: list[RuleFinding] = []
        for rule in sorted(RULES, key=lambda item: item.rule_id):
            findings.extend(rule.evaluate(trace))
        sorted_findings = tuple(sorted(findings, key=lambda item: item.sort_key()))
        summary = summary_from_findings(sorted_findings, evaluated_rule_count=len(RULES))
        return EvaluationResult(
            evaluator_version=EVALUATOR_VERSION,
            ruleset_version=RULESET_VERSION,
            evaluated_at=now_utc(),
            trace_id=trace.trace_id,
            verdict=verdict_from_findings(sorted_findings),
            findings=sorted_findings,
            summary=summary,
            source=trace.source,
            input_schema_version=trace.schema_version,
        )
    except Exception as exc:
        if isinstance(exc, EvaluatorInternalError):
            raise
        raise EvaluatorInternalError("Unexpected evaluator failure.") from exc
