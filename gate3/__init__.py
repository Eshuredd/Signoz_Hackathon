"""TraceGuard Gate 3 deterministic telemetry evaluator."""

from .evaluator import evaluate_run_bundle, evaluate_trace
from .models import (
    EvaluationResult,
    EvaluationSummary,
    RuleResult,
    RuleStatus,
    RunBundle,
    Severity,
    Verdict,
)
from .trace_loader import RunBundleInputError, TraceInputError, load_run_bundle_file, load_trace_file

__all__ = [
    "EvaluationResult",
    "EvaluationSummary",
    "RuleResult",
    "RuleStatus",
    "RunBundle",
    "RunBundleInputError",
    "Severity",
    "TraceInputError",
    "Verdict",
    "evaluate_run_bundle",
    "evaluate_trace",
    "load_run_bundle_file",
    "load_trace_file",
]
