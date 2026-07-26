from __future__ import annotations

from typing import Any

from gate3.trace_loader import load_run_bundle_payload
from gate3_preflight.bridge import gate2_trace_to_gate3_envelope

from .models import RetrievedLog


def signoz_log_to_gate3_log_record(log: RetrievedLog) -> dict[str, object]:
    return {
        "timestamp": log.timestamp,
        "trace_id": log.trace_id,
        "span_id": log.span_id,
        "attributes": dict(log.attributes),
        "body": log.body,
    }


def build_gate3_run_bundle(
    agent_run_id: str,
    retrieved_traces: tuple[object, ...] | list[object],
    retrieved_logs: tuple[RetrievedLog, ...] | list[RetrievedLog],
    metadata: dict[str, Any],
) -> dict[str, object]:
    payload = {
        "schema_version": 1,
        "agent_run_id": agent_run_id,
        "traces": [gate2_trace_to_gate3_envelope(trace) for trace in retrieved_traces],
        "logs": [signoz_log_to_gate3_log_record(log) for log in retrieved_logs],
        "metadata": dict(metadata),
    }
    load_run_bundle_payload(payload)
    return payload

