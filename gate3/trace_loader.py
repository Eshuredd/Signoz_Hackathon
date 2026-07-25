from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .models import (
        SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION,
        SUPPORTED_TRACE_INPUT_SCHEMA_VERSION,
        LogRecord,
        NormalizedTrace,
        RunBundle,
        Span,
        is_valid_integer,
    )
except ImportError:  # pragma: no cover
    from models import (
        SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION,
        SUPPORTED_TRACE_INPUT_SCHEMA_VERSION,
        LogRecord,
        NormalizedTrace,
        RunBundle,
        Span,
        is_valid_integer,
    )


STRING_FIELDS = ("trace_id", "span_id", "span_name", "start_time", "end_time", "service_name")


class TraceInputError(Exception):
    """Raised when normalized trace input is malformed."""


class RunBundleInputError(Exception):
    """Raised when normalized run-bundle input is malformed."""


def load_trace_file(path: str | Path) -> NormalizedTrace:
    return load_trace_payload(_read_json(Path(path), "trace input", TraceInputError))


def load_run_bundle_file(path: str | Path) -> RunBundle:
    return load_run_bundle_payload(_read_json(Path(path), "run bundle input", RunBundleInputError))


def load_trace_payload(payload: Any) -> NormalizedTrace:
    if not isinstance(payload, dict):
        raise TraceInputError("Trace input top-level value must be an object.")
    schema_version = payload.get("schema_version")
    if not is_valid_integer(schema_version):
        raise TraceInputError("Trace input schema_version must be an integer.")
    if schema_version != SUPPORTED_TRACE_INPUT_SCHEMA_VERSION:
        raise TraceInputError(f"Unsupported trace input schema_version: {schema_version!r}.")
    trace = payload.get("trace")
    if not isinstance(trace, dict):
        raise TraceInputError("Trace input must contain a trace object.")
    return _load_trace_object(schema_version, trace, error_cls=TraceInputError)


def load_run_bundle_payload(payload: Any) -> RunBundle:
    if not isinstance(payload, dict):
        raise RunBundleInputError("Run bundle top-level value must be an object.")
    schema_version = payload.get("schema_version")
    if not is_valid_integer(schema_version):
        raise RunBundleInputError("Run bundle schema_version must be an integer.")
    if schema_version != SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION:
        raise RunBundleInputError(f"Unsupported run bundle schema_version: {schema_version!r}.")
    agent_run_id = payload.get("agent_run_id")
    if not isinstance(agent_run_id, str) or not agent_run_id.strip():
        raise RunBundleInputError("Run bundle agent_run_id must be a non-empty string.")
    traces_payload = payload.get("traces")
    if not isinstance(traces_payload, list):
        raise RunBundleInputError("Run bundle traces must be a list.")
    if not traces_payload:
        raise RunBundleInputError("Run bundle traces must contain at least one trace.")
    traces: list[NormalizedTrace] = []
    for index, item in enumerate(traces_payload):
        try:
            traces.append(load_trace_payload(item))
        except TraceInputError as exc:
            raise RunBundleInputError(f"Run bundle traces[{index}] is invalid: {exc}") from exc
    logs_payload = payload.get("logs", [])
    if not isinstance(logs_payload, list):
        raise RunBundleInputError("Run bundle logs must be a list when present.")
    logs = tuple(_load_log_record(index, item) for index, item in enumerate(logs_payload))
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise RunBundleInputError("Run bundle metadata must be an object when present.")
    return RunBundle(schema_version=schema_version, agent_run_id=agent_run_id, traces=tuple(traces), logs=logs, metadata=metadata)


def _read_json(path: Path, label: str, error_cls: type[Exception]) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise error_cls(f"Unable to read {label}: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise error_cls(f"Invalid JSON in {label}: {path}") from exc


def _load_trace_object(schema_version: int, trace: dict[str, Any], *, error_cls: type[Exception]) -> NormalizedTrace:
    spans = trace.get("spans")
    if not isinstance(spans, list):
        raise error_cls("trace.spans must be a list.")
    trace_id = trace.get("trace_id", "")
    if trace_id is not None and not isinstance(trace_id, str):
        raise error_cls("trace.trace_id must be a string when present.")
    retrieved_at = trace.get("retrieved_at")
    if retrieved_at is not None:
        _require_string("trace.retrieved_at", retrieved_at, error_cls)
        _parse_timestamp("trace.retrieved_at", retrieved_at, error_cls)
    source = trace.get("source")
    if source is not None:
        _require_string("trace.source", source, error_cls)
    metadata = trace.get("metadata", {})
    if not isinstance(metadata, dict):
        raise error_cls("trace.metadata must be an object when present.")

    normalized_spans: list[Span] = []
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise error_cls(f"trace.spans[{index}] must be an object.")
        _validate_span(span, index, error_cls)
        normalized_spans.append(Span(raw=dict(span), index=index))
    return NormalizedTrace(schema_version=schema_version, trace_id=trace_id or "", spans=tuple(normalized_spans), retrieved_at=retrieved_at, source=source, metadata=metadata)


def _load_log_record(index: int, payload: Any) -> LogRecord:
    if not isinstance(payload, dict):
        raise RunBundleInputError(f"logs[{index}] must be an object.")
    timestamp = payload.get("timestamp")
    if timestamp is not None:
        _require_string(f"logs[{index}].timestamp", timestamp, RunBundleInputError)
        _parse_timestamp(f"logs[{index}].timestamp", timestamp, RunBundleInputError)
    trace_id = payload.get("trace_id")
    if trace_id is not None:
        _require_string(f"logs[{index}].trace_id", trace_id, RunBundleInputError)
    span_id = payload.get("span_id")
    if span_id is not None:
        _require_string(f"logs[{index}].span_id", span_id, RunBundleInputError)
    attributes = payload.get("attributes", {})
    if not isinstance(attributes, dict):
        raise RunBundleInputError(f"logs[{index}].attributes must be an object when present.")
    return LogRecord(index=index, timestamp=timestamp, trace_id=trace_id, span_id=span_id, attributes=attributes, body=payload.get("body"))


def _validate_span(span: dict[str, Any], index: int, error_cls: type[Exception]) -> None:
    for field in STRING_FIELDS:
        if field in span and span[field] is not None:
            _require_string(f"trace.spans[{index}].{field}", span[field], error_cls)
    if "parent_span_id" in span and span["parent_span_id"] is not None:
        _require_string(f"trace.spans[{index}].parent_span_id", span["parent_span_id"], error_cls)
    for field in ("attributes", "resource_attributes", "status"):
        if field in span and not isinstance(span[field], dict):
            raise error_cls(f"trace.spans[{index}].{field} must be an object when present.")
    for field in ("start_time", "end_time"):
        if field in span and span[field] is not None:
            _parse_timestamp(f"trace.spans[{index}].{field}", span[field], error_cls)
    if "duration_nano" in span and span["duration_nano"] is not None:
        if not is_valid_integer(span["duration_nano"]):
            raise error_cls(f"trace.spans[{index}].duration_nano must be an integer when present.")


def _require_string(path: str, value: Any, error_cls: type[Exception]) -> None:
    if not isinstance(value, str):
        raise error_cls(f"{path} must be a string.")


def _parse_timestamp(path: str, value: str, error_cls: type[Exception]) -> datetime:
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise error_cls(f"{path} must be a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise error_cls(f"{path} must include timezone information.")
    return parsed
