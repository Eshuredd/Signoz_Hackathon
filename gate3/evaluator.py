from __future__ import annotations

try:
    from .models import (
        EVALUATOR_VERSION,
        RULESET_VERSION,
        EvaluationLevel,
        EvaluationResult,
        NormalizedTrace,
        RuleResult,
        RuleStatus,
        RunBundle,
        now_utc,
        summary_from_rule_results,
        verdict_from_rule_results,
    )
    from .rules import RULES
except ImportError:  # pragma: no cover
    from models import (
        EVALUATOR_VERSION,
        RULESET_VERSION,
        EvaluationLevel,
        EvaluationResult,
        NormalizedTrace,
        RuleResult,
        RuleStatus,
        RunBundle,
        now_utc,
        summary_from_rule_results,
        verdict_from_rule_results,
    )
    from rules import RULES


class EvaluatorInternalError(Exception):
    """Raised for unexpected evaluator defects."""


def evaluate_trace(trace: NormalizedTrace, *, debug: bool = False) -> EvaluationResult:
    results = _evaluate_target(trace, EvaluationLevel.TRACE, debug=debug)
    return EvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        ruleset_version=RULESET_VERSION,
        evaluated_at=now_utc(),
        evaluation_level=EvaluationLevel.TRACE,
        agent_run_id=_agent_run_id_from_trace(trace),
        trace_ids=(trace.trace_id,),
        verdict=verdict_from_rule_results(results),
        rule_results=results,
        summary=summary_from_rule_results(results),
        source=trace.source,
        input_schema_version=trace.schema_version,
    )


def evaluate_run_bundle(bundle: RunBundle, *, debug: bool = False) -> EvaluationResult:
    per_trace_results: list[RuleResult] = []
    trace_level_rules = [rule for rule in RULES if rule.evaluation_level == EvaluationLevel.TRACE]
    for rule in sorted(trace_level_rules, key=lambda item: (item.namespace, item.rule_id)):
        try:
            child_results = [rule.evaluate(trace) for trace in bundle.traces]
            per_trace_results.append(_aggregate_trace_results(rule.rule_id, child_results))
        except Exception as exc:  # pragma: no cover - defensive contract
            per_trace_results.append(_error_result(rule.rule_id, exc, debug))
    run_results = _evaluate_target(bundle, EvaluationLevel.RUN, debug=debug, include_trace_rules=False)
    results = tuple(sorted([*run_results, *per_trace_results], key=lambda item: item.sort_key()))
    trace_ids = tuple(sorted({trace.trace_id for trace in bundle.traces if trace.trace_id}))
    return EvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        ruleset_version=RULESET_VERSION,
        evaluated_at=now_utc(),
        evaluation_level=EvaluationLevel.RUN,
        agent_run_id=bundle.agent_run_id,
        trace_ids=trace_ids,
        verdict=verdict_from_rule_results(results),
        rule_results=results,
        summary=summary_from_rule_results(results),
        source="run-bundle",
        input_schema_version=bundle.schema_version,
    )


def _evaluate_target(
    target: NormalizedTrace | RunBundle,
    level: EvaluationLevel,
    *,
    debug: bool,
    include_trace_rules: bool = True,
) -> tuple[RuleResult, ...]:
    results: list[RuleResult] = []
    for rule in sorted(RULES, key=lambda item: (item.namespace, item.rule_id)):
        if level == EvaluationLevel.RUN and rule.evaluation_level == EvaluationLevel.TRACE and not include_trace_rules:
            continue
        try:
            results.append(rule.evaluate(target))
        except Exception as exc:  # pragma: no cover - defensive contract
            results.append(_error_result(rule.rule_id, exc, debug))
    return tuple(sorted(results, key=lambda item: item.sort_key()))


def _aggregate_trace_results(rule_id: str, results: list[RuleResult]) -> RuleResult:
    if not results:
        from .rules import RULE_BY_ID

        rule = RULE_BY_ID[rule_id]
        return rule.result(RuleStatus.NOT_APPLICABLE, "Run bundle contains no traces.", observed={"trace_count": 0})
    first = results[0]
    if any(result.status == RuleStatus.EVALUATION_ERROR for result in results):
        status = RuleStatus.EVALUATION_ERROR
    elif any(result.status == RuleStatus.FAILED for result in results):
        status = RuleStatus.FAILED
    elif all(result.status == RuleStatus.NOT_APPLICABLE for result in results):
        status = RuleStatus.NOT_APPLICABLE
    else:
        status = RuleStatus.PASSED
    failed = [result for result in results if result.status in {RuleStatus.FAILED, RuleStatus.EVALUATION_ERROR}]
    return RuleResult(
        rule_id=first.rule_id,
        rule_name=first.rule_name,
        namespace=first.namespace,
        severity=first.severity,
        status=status,
        message=f"Aggregated {first.rule_id} across {len(results)} trace(s).",
        expected=first.expected,
        observed={"trace_results": [{"trace_ids": list(result.affected_trace_ids), "status": result.status.value, "observed": result.observed} for result in results]},
        evidence={"trace_results": [result.to_dict() for result in results]},
        affected_span_ids=tuple(sorted({span_id for result in failed for span_id in result.affected_span_ids})),
        affected_trace_ids=tuple(sorted({trace_id for result in failed for trace_id in result.affected_trace_ids})),
        deterministic=first.deterministic,
        documentation=first.documentation,
    )


def _error_result(rule_id: str, exc: Exception, debug: bool) -> RuleResult:
    from .rules import RULE_BY_ID

    rule = RULE_BY_ID[rule_id]
    evidence = {"error_type": exc.__class__.__name__}
    if debug:
        evidence["error"] = str(exc)
    return rule.result(
        RuleStatus.EVALUATION_ERROR,
        "Rule implementation raised an evaluation error.",
        observed={"error_type": exc.__class__.__name__},
        evidence=evidence,
    )


def _agent_run_id_from_trace(trace: NormalizedTrace) -> str | None:
    roots = [span for span in trace.spans if span.span_name == "agent.run"]
    if len(roots) != 1:
        return None
    value = roots[0].attributes.get("agent.run_id")
    return value if isinstance(value, str) and value.strip() else None
