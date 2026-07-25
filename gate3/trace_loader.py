from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .models import NormalizedTrace, Span, is_valid_integer
except ImportError:  # pragma: no cover - supports direct script imports.
    from models import NormalizedTrace, Span, is_valid_integer


SUPPORTED_SCHEMA_VERSIONS = {1}
STRING_FIELDS = ("trace_id", "span_id", "span_name", "start_time", "end_time", "service_name")


class TraceInputError(Exception):
    """Raised when normalized trace input is malformed."""


def load_trace_file(path: str | Path) -> NormalizedTrace:
    trace_path = Path(path)
    try:
        text = trace_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TraceInputError(f"Unable to read trace input: {trace_path}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TraceInputError(f"Invalid JSON in trace input: {trace_path}") from exc
    return load_trace_payload(payload)


def load_trace_payload(payload: Any) -> NormalizedTrace:
    if not isinstance(payload, dict):
        raise TraceInputError("Trace input top-level value must be an object.")
    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise TraceInputError(f"Unsupported trace input schema_version: {schema_version!r}.")
    trace = payload.get("trace")
    if not isinstance(trace, dict):
        raise TraceInputError("Trace input must contain a trace object.")

    spans = trace.get("spans")
    if not isinstance(spans, list):
        raise TraceInputError("trace.spans must be a list.")

    trace_id = trace.get("trace_id", "")
    if trace_id is not None and not isinstance(trace_id, str):
        raise TraceInputError("trace.trace_id must be a string when present.")
    retrieved_at = trace.get("retrieved_at")
    if retrieved_at is not None:
        _require_string("trace.retrieved_at", retrieved_at)
        _parse_timestamp("trace.retrieved_at", retrieved_at)
    source = trace.get("source")
    if source is not None:
        _require_string("trace.source", source)
    metadata = trace.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TraceInputError("trace.metadata must be an object when present.")

    normalized_spans: list[Span] = []
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise TraceInputError(f"trace.spans[{index}] must be an object.")
        _validate_span(span, index)
        normalized_spans.append(Span(raw=dict(span), index=index))

    return NormalizedTrace(
        schema_version=schema_version,
        trace_id=trace_id or "",
        spans=tuple(normalized_spans),
        retrieved_at=retrieved_at,
        source=source,
        metadata=metadata,
    )


def _validate_span(span: dict[str, Any], index: int) -> None:
    for field in STRING_FIELDS:
        if field in span and span[field] is not None:
            _require_string(f"trace.spans[{index}].{field}", span[field])
    if "parent_span_id" in span and span["parent_span_id"] is not None:
        _require_string(f"trace.spans[{index}].parent_span_id", span["parent_span_id"])
    for field in ("attributes", "resource_attributes", "status"):
        if field in span and not isinstance(span[field], dict):
            raise TraceInputError(f"trace.spans[{index}].{field} must be an object when present.")
    for field in ("start_time", "end_time"):
        if field in span and span[field] is not None:
            _parse_timestamp(f"trace.spans[{index}].{field}", span[field])
    if "duration_nano" in span and span["duration_nano"] is not None:
        if not is_valid_integer(span["duration_nano"]):
            raise TraceInputError(f"trace.spans[{index}].duration_nano must be an integer when present.")


def _require_string(path: str, value: Any) -> None:
    if not isinstance(value, str):
        raise TraceInputError(f"{path} must be a string.")


def _parse_timestamp(path: str, value: str) -> datetime:
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise TraceInputError(f"{path} must be a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise TraceInputError(f"{path} must include timezone information.")
    return parsed
