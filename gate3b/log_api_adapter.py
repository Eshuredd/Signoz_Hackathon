from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
import sys
from pathlib import Path
from typing import Any, Callable

GATE2_DIR = Path(__file__).resolve().parents[1] / "gate2"
if str(GATE2_DIR) not in sys.path:
    sys.path.insert(0, str(GATE2_DIR))

from signoz_api_client import SigNozAPIClient, build_filter_expression, extract_query_rows  # type: ignore[import-not-found]
from exceptions import AuthenticationFailure, AuthorizationFailure, ConfigurationError, ConnectionFailure, EmptySearchResults, InvalidResponseSchema, RequestTimeout, UnsupportedAPIOperation  # type: ignore[import-not-found]

from .models import LOG_ID_ATTR, TRACE_SCENARIO_ATTR, Gate3BInfrastructureError, LogRetrievalResult, RetrievedLog, RetrievalStats, RuntimeScenario


NON_RETRY = (AuthenticationFailure, AuthorizationFailure, ConfigurationError, InvalidResponseSchema, UnsupportedAPIOperation, ConnectionFailure, RequestTimeout)


class Gate3BLogRetrievalError(Gate3BInfrastructureError):
    """Log retrieval failed."""


def verify_log_api_access(client: SigNozAPIClient) -> bool:
    rows, _ = search_logs(client, {TRACE_SCENARIO_ATTR: f"auth-check-{datetime.now(UTC).timestamp()}"}, limit=1)
    return rows == []


def search_logs(client: SigNozAPIClient, attribute_filters: dict[str, Any], *, limit: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = datetime.now(UTC)
    payload = {
        "schemaVersion": "v1",
        "start": int((now - timedelta(minutes=15)).timestamp() * 1000),
        "end": int((now + timedelta(minutes=5)).timestamp() * 1000),
        "requestType": "raw",
        "variables": {},
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "logs",
                        "filter": {"expression": build_filter_expression(attribute_filters)},
                        "order": [{"key": {"name": "timestamp", "fieldContext": "log"}, "direction": "desc"}],
                        "limit": limit,
                        "offset": 0,
                        "disabled": False,
                    },
                }
            ]
        },
    }
    response = client._request_json("POST", "/api/v5/query_range", json_body=payload)  # type: ignore[attr-defined]
    return extract_query_rows(response), response


def poll_and_retrieve_logs(
    client: SigNozAPIClient,
    scenario: RuntimeScenario,
    expected_log_ids: tuple[str, ...],
    *,
    timeout_seconds: float,
    interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> LogRetrievalResult:
    if scenario.definition.expected_log_count == 0:
        return LogRetrievalResult((), RetrievalStats(0, 0, 0, "zero_log_scenario"))
    start = monotonic()
    deadline = start + timeout_seconds
    attempts = 0
    last_retry_reason: str | None = None
    expected = set(expected_log_ids)
    while monotonic() <= deadline:
        try:
            attempts += 1
            rows, _raw = search_logs(client, {TRACE_SCENARIO_ATTR: scenario.scenario_id}, limit=100)
            logs = [normalize_log_row(row) for row in rows]
            by_id: dict[str, RetrievedLog] = {}
            for log in logs:
                if log.attributes.get(TRACE_SCENARIO_ATTR) != scenario.scenario_id:
                    raise Gate3BLogRetrievalError("Retrieved log scenario ID mismatch.")
                if log.log_id not in expected:
                    raise Gate3BLogRetrievalError("Unexpected log ID retrieved for scenario.")
                previous = by_id.get(log.log_id)
                if previous and previous.to_dict() != log.to_dict():
                    raise Gate3BLogRetrievalError("Duplicate log ID had contradictory content.")
                by_id[log.log_id] = log
            if set(by_id) == expected:
                return LogRetrievalResult(tuple(by_id[key] for key in sorted(by_id)), RetrievalStats(attempts, attempts, int((monotonic() - start) * 1000), last_retry_reason))
            last_retry_reason = "fewer_unique_logs_than_expected"
        except EmptySearchResults:
            last_retry_reason = "authenticated_empty_log_result"
        except NON_RETRY:
            raise
        if monotonic() >= deadline:
            break
        sleeper(min(interval_seconds, max(0.0, deadline - monotonic())))
    raise Gate3BLogRetrievalError(f"Timed out waiting for log ingestion: {last_retry_reason}; elapsed_ms={int((monotonic() - start) * 1000)}")


def normalize_log_row(row: dict[str, Any]) -> RetrievedLog:
    data = row.get("data") if isinstance(row.get("data"), dict) else row
    if not isinstance(data, dict):
        raise InvalidResponseSchema("Log query row must contain an object data payload.")
    attrs = _merged_dicts(
        data.get("attributes"),
        data.get("attrs"),
        data.get("attributes_string"),
        data.get("attributes_number"),
        data.get("attributes_bool"),
    )
    resource = _merged_dicts(data.get("resource"), data.get("resources"), data.get("resource_attributes"), data.get("resources_string"))
    log_id = attrs.get(LOG_ID_ATTR) or data.get(LOG_ID_ATTR)
    trace_id = data.get("trace_id") or attrs.get("trace_id")
    span_id = data.get("span_id") or attrs.get("span_id")
    body = data.get("body")
    if body is None:
        body = data.get("message") or data.get("msg") or data.get("log")
    timestamp = data.get("timestamp") or data.get("time") or data.get("time_unix")
    service_name = resource.get("service.name") or attrs.get("service.name") or data.get("service_name")
    if not log_id or body is None:
        raise InvalidResponseSchema("Normalized log is missing required log_id or body.")
    return RetrievedLog(str(log_id), _timestamp_to_iso(timestamp), str(trace_id) if trace_id else None, str(span_id) if span_id else None, body, attrs, resource, str(service_name) if service_name else None)


def log_api_contract(signoz_version: str) -> dict[str, object]:
    return {
        "signoz_version": signoz_version,
        "authenticated_api_path_used": "/api/v5/query_range",
        "query_signal_type": "logs",
        "scenario_attribute_filter": TRACE_SCENARIO_ATTR,
        "normalized_fields": ["log_id", "timestamp", "trace_id", "span_id", "body", "attributes", "resource_attributes", "service_name"],
        "timestamp_source": "data.timestamp | row.timestamp | data.time | data.time_unix",
        "trace_id_source": "data.trace_id when present, otherwise attributes_string.trace_id",
        "span_id_source": "data.span_id when present, otherwise attributes_string.span_id",
        "attribute_source": "data.attributes | data.attrs | data.attributes_string | data.attributes_number | data.attributes_bool",
        "resource_attribute_source": "data.resource | data.resources | data.resource_attributes | data.resources_string",
        "body_source": "data.body | data.message | data.msg | data.log",
        "sanitized": True,
    }


def _ensure_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _merged_dicts(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        merged.update(_ensure_dict(value))
    return merged


def _timestamp_to_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    absolute = abs(numeric)
    if absolute > 10**17:
        seconds = numeric / 1_000_000_000
    elif absolute > 10**14:
        seconds = numeric / 1_000_000
    elif absolute > 10**11:
        seconds = numeric / 1_000
    else:
        seconds = numeric
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")
