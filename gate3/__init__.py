"""Gate 3A deterministic telemetry evaluator."""

from .evaluator import evaluate_trace
from .models import (
    EvaluationResult,
    EvaluationSummary,
    RuleFinding,
    Severity,
    Verdict,
)
from .trace_loader import TraceInputError, load_trace_file

__all__ = [
    "EvaluationResult",
    "EvaluationSummary",
    "RuleFinding",
    "Severity",
    "TraceInputError",
    "Verdict",
    "evaluate_trace",
    "load_trace_file",
]
