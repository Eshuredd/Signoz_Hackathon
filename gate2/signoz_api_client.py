from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests import Response

from config import Gate2Config
from exceptions import (
    AuthenticationFailure,
    AuthorizationFailure,
    ConfigurationError,
    ConnectionFailure,
    EmptySearchResults,
    Gate2Error,
    InvalidResponseSchema,
    RequestTimeout,
    TraceNotFound,
    UnsupportedAPIOperation,
)
from logging_config import configure_logging
from models import (
    CapabilityAssessment,
    CapabilityState,
    ProbeEvidence,
    Source,
    Span,
    Trace,
    TraceSearchHit,
    classify_trace_structure,
    deterministic_assessment,
    now_utc,
    relationship_capabilities,
)


FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SigNozAPIClient:
    def __init__(self, config: Gate2Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.session = requests.Session()

    def health_check(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/v1/health", auth=False)

    def version(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/v1/version", auth=False)

    def auth_required_check(self) -> bool:
        response = self.session.post(
            f"{self.config.signoz_base_url}/api/v5/query_range",
            headers={"Content-Type": "application/json"},
            json={},
            timeout=self.config.request_timeout_seconds,
        )
        return response.status_code == 401

    def get_trace(self, trace_id: str) -> tuple[Trace, dict[str, Any]]:
        path = f"/api/v4/traces/{trace_id}/waterfall"
        started = time.perf_counter()
        payload = {"selectedSpanId": "", "uncollapsedSpans": []}
        response_json = self._request_json("POST", path, json_body=payload)
        data = unwrap_success_data(response_json)
        trace = parse_waterfall_trace(data, Source.TRACE_API)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "trace_api_direct_lookup_completed",
            extra={
                "_source": Source.TRACE_API.value,
                "_operation": "get_trace",
                "_trace_id": trace_id,
                "_span_count": len(trace.spans),
                "_elapsed_ms": elapsed_ms,
            },
        )
        return trace, response_json

    def search_traces(
        self,
        attribute_filters: dict[str, Any],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 20,
    ) -> tuple[list[TraceSearchHit], dict[str, Any]]:
        if not attribute_filters:
            raise ConfigurationError("search_traces requires at least one attribute filter.")
        if limit <= 0:
            raise ConfigurationError("search_traces limit must be greater than zero.")

        started = time.perf_counter()
        now = datetime.now(UTC)
        start_time = start_time or now - timedelta(hours=24)
        end_time = end_time or now + timedelta(minutes=5)
        payload = {
            "schemaVersion": "v1",
            "start": int(start_time.timestamp() * 1000),
            "end": int(end_time.timestamp() * 1000),
            "requestType": "raw",
            "variables": {},
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "traces",
                            "filter": {
                                "expression": build_filter_expression(attribute_filters)
                            },
                            "order": [
                                {
                                    "key": {
                                        "name": "timestamp",
                                        "fieldContext": "span",
                                    },
                                    "direction": "desc",
                                }
                            ],
                            "limit": limit,
                            "offset": 0,
                            "disabled": False,
                        },
                    }
                ]
            },
        }

        response_json = self._request_json("POST", "/api/v5/query_range", json_body=payload)
        rows = extract_query_rows(response_json)
        if not rows:
            raise EmptySearchResults(
                "Trace search returned no rows for filters: "
                + ", ".join(attribute_filters.keys())
            )

        hits = [parse_search_hit(row) for row in rows]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "trace_api_attribute_search_completed",
            extra={
                "_source": Source.TRACE_API.value,
                "_operation": "search_traces",
                "_result_count": len(hits),
                "_elapsed_ms": elapsed_ms,
            },
        )
        return hits, response_json

    def find_trace_by_run_id(self, run_id: str) -> tuple[list[TraceSearchHit], dict[str, Any]]:
        return self.search_traces({"agent.run_id": run_id})

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        if auth and not self.config.signoz_api_key:
            raise AuthenticationFailure(
                "SIGNOZ_API_KEY is required for SigNoz trace/query API calls. "
                "Create a service account key in SigNoz and export it before running Gate 2."
            )

        url = f"{self.config.signoz_base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if auth and self.config.signoz_api_key:
            headers["SIGNOZ-API-KEY"] = self.config.signoz_api_key

        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise RequestTimeout(f"{method} {url} timed out.") from exc
        except requests.ConnectionError as exc:
            raise ConnectionFailure(f"Could not connect to {url}.") from exc

        return parse_json_response(response, method, url)


def parse_json_response(response: Response, method: str, url: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise InvalidResponseSchema(
            f"{method} {url} returned non-JSON response with status {response.status_code}."
        ) from exc

    if response.status_code == 401:
        raise AuthenticationFailure(extract_error_message(payload, "unauthenticated"))
    if response.status_code == 403:
        raise AuthorizationFailure(extract_error_message(payload, "forbidden"))
    if response.status_code == 404:
        raise classify_not_found(payload, method, url)
    if response.status_code >= 400:
        raise InvalidResponseSchema(
            f"{method} {url} failed with status {response.status_code}: "
            f"{extract_error_message(payload, 'request failed')}"
        )

    return payload


def classify_not_found(payload: dict[str, Any], method: str, url: str) -> Gate2Error:
    message = extract_error_message(payload, "not found")
    path = urlparse(url).path
    lowered = message.lower()
    trace_not_found_terms = (
        "trace not found",
        "traceid not found",
        "trace id not found",
        "no trace found",
        "trace not exist",
    )
    if "/api/v4/traces/" in path and path.endswith("/waterfall"):
        if any(term in lowered for term in trace_not_found_terms):
            return TraceNotFound(message)
        return UnsupportedAPIOperation(
            f"{method} {path} returned HTTP 404; the endpoint may be unsupported, "
            f"incompatible with this SigNoz version, or routed incorrectly: {message}"
        )
    if path.endswith("/api/v5/query_range"):
        return UnsupportedAPIOperation(
            f"{method} {path} returned HTTP 404; query_range may be unavailable "
            f"for this SigNoz version or route: {message}"
        )
    return UnsupportedAPIOperation(
        f"{method} {path} returned HTTP 404; endpoint unavailable or wrong route: {message}"
    )


def extract_error_message(payload: dict[str, Any], fallback: str) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if message and code:
            return f"{code}: {message}"
        if message:
            return str(message)
    return fallback


def unwrap_success_data(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") == "success" and isinstance(payload.get("data"), dict):
        return payload["data"]
    if "spans" in payload:
        return payload
    raise InvalidResponseSchema("SigNoz response did not match the success data wrapper.")


def parse_waterfall_trace(data: dict[str, Any], source: Source) -> Trace:
    spans_data = data.get("spans")
    if not isinstance(spans_data, list):
        raise InvalidResponseSchema("Waterfall response data.spans must be a list.")
    if not spans_data:
        raise TraceNotFound("Waterfall response contained no spans for the requested trace.")

    root_service_name = data.get("rootServiceName")
    spans = [parse_waterfall_span(span, root_service_name) for span in spans_data]
    trace_ids = {span.trace_id for span in spans if span.trace_id}
    trace_id = next(iter(trace_ids)) if trace_ids else ""
    if not trace_id:
        raise InvalidResponseSchema("Waterfall spans did not contain trace_id.")

    return Trace(
        trace_id=trace_id,
        spans=spans,
        retrieved_at=now_utc(),
        source=source,
        metadata={
            "root_service_name": root_service_name,
            "root_service_entry_point": data.get("rootServiceEntryPoint"),
            "total_spans_count": data.get("totalSpansCount"),
            "total_error_spans_count": data.get("totalErrorSpansCount"),
            "has_missing_spans": data.get("hasMissingSpans"),
            "has_more": data.get("hasMore"),
        },
    )


def parse_waterfall_span(raw: dict[str, Any], root_service_name: str | None) -> Span:
    if not isinstance(raw, dict):
        raise InvalidResponseSchema("Each waterfall span must be an object.")

    start = parse_timestamp(raw.get("time_unix") or raw.get("timestamp"))
    duration_nano = parse_int(raw.get("duration_nano"))
    end = (
        start + timedelta(microseconds=duration_nano / 1000)
        if start and duration_nano is not None
        else None
    )
    attributes = ensure_dict(raw.get("attributes"))
    resource = ensure_dict(raw.get("resource"))
    service_name = resource.get("service.name") or root_service_name
    status = {
        "status_code": raw.get("status_code"),
        "status_code_string": raw.get("status_code_string"),
        "status_message": raw.get("status_message"),
        "has_error": raw.get("has_error"),
    }
    status = {key: value for key, value in status.items() if value is not None}

    return Span(
        trace_id=str(raw.get("trace_id") or ""),
        span_id=str(raw.get("span_id") or ""),
        parent_span_id=str(raw.get("parent_span_id")) if raw.get("parent_span_id") is not None else None,
        span_name=str(raw.get("name") or ""),
        start_time=start,
        end_time=end,
        duration_nano=duration_nano,
        status=status,
        attributes=attributes,
        resource_attributes=resource,
        service_name=str(service_name) if service_name else None,
        raw=raw,
    )


def parse_timestamp(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            pass
    try:
        numeric = float(raw)
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
    return datetime.fromtimestamp(seconds, tz=UTC)


def parse_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def ensure_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def build_filter_expression(filters: dict[str, Any]) -> str:
    parts = []
    for key, value in filters.items():
        if not FIELD_NAME_RE.fullmatch(key):
            raise ConfigurationError(f"Unsupported SigNoz filter field name: {key!r}.")
        parts.append(f"{key} = {format_filter_value(value)}")
    return " AND ".join(parts)


def format_filter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def extract_query_rows(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    data = unwrap_success_data(response_json)
    query_data = data.get("data")
    if isinstance(query_data, dict) and isinstance(query_data.get("results"), list):
        results = query_data["results"]
    elif isinstance(data.get("results"), list):
        results = data["results"]
    else:
        raise InvalidResponseSchema(
            "Query response did not contain data.data.results or data.results."
        )

    if not results:
        return []
    rows = results[0].get("rows")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise InvalidResponseSchema("Query response rows must be a list or null.")
    return rows


def parse_search_hit(row: dict[str, Any]) -> TraceSearchHit:
    if not isinstance(row, dict) or not isinstance(row.get("data"), dict):
        raise InvalidResponseSchema("Each query row must contain a data object.")

    data = row["data"]
    attributes = ensure_dict(data.get("attributes"))
    resource = ensure_dict(data.get("resource"))
    trace_id = data.get("trace_id")
    if not trace_id:
        raise InvalidResponseSchema("Trace search hit did not contain trace_id.")

    return TraceSearchHit(
        trace_id=str(trace_id),
        span_id=str(data.get("span_id")) if data.get("span_id") is not None else None,
        span_name=str(data.get("name")) if data.get("name") is not None else None,
        attributes=attributes,
        resource_attributes=resource,
        raw=data,
    )


def write_json_artifact(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    return str(path)


def run_trace_api_probe(
    config: Gate2Config,
    logger: logging.Logger,
    artifacts_dir: Path,
) -> ProbeEvidence:
    evidence = ProbeEvidence(
        source=Source.TRACE_API,
        available=False,
        non_secret_config=config.non_secret_snapshot(),
        commands_attempted=[
            f"curl -fsS {config.signoz_base_url}/api/v1/health",
            f"curl -fsS {config.signoz_base_url}/api/v1/version",
            "curl -X POST <SIGNOZ_BASE_URL>/api/v4/traces/<trace_id>/waterfall "
            "-H 'SIGNOZ-API-KEY: <redacted>'",
            "curl -X POST <SIGNOZ_BASE_URL>/api/v5/query_range "
            "-H 'SIGNOZ-API-KEY: <redacted>'",
        ],
    )
    client = SigNozAPIClient(config, logger)

    try:
        health = client.health_check()
        evidence.available = health.get("status") == "ok"
        evidence.observations["health_check"] = "succeeded"
        write_json_artifact(artifacts_dir / "trace_api_health.json", health)
    except Exception as exc:
        record_probe_error(evidence, logger, config, "health_check", exc)

    try:
        version = client.version()
        evidence.installed_signoz_version = str(version.get("version") or "unknown")
        evidence.observations["version_check"] = "succeeded"
        write_json_artifact(artifacts_dir / "trace_api_version.json", version)
    except Exception as exc:
        record_probe_error(evidence, logger, config, "version_check", exc)

    try:
        if client.auth_required_check():
            evidence.authentication_required = CapabilityAssessment(
                "authentication required",
                CapabilityState.OBSERVED,
                "protected trace/query endpoints return 401 without SIGNOZ-API-KEY",
            )
        else:
            evidence.authentication_required = CapabilityAssessment(
                "authentication required",
                CapabilityState.NOT_OBSERVED,
                "protected endpoint did not return 401 in the no-key probe",
            )
        evidence.observations["authentication_requirement_check"] = "succeeded"
    except Exception as exc:
        evidence.authentication_required = CapabilityAssessment(
            "authentication required",
            CapabilityState.NOT_OBSERVED,
            f"{exc.__class__.__name__}: {exc}",
        )
        record_probe_error(evidence, logger, config, "authentication_requirement_check", exc)

    trace: Trace | None = None
    if config.signoz_trace_id:
        if config.signoz_api_key:
            try:
                trace, raw_trace = client.get_trace(config.signoz_trace_id)
                raw_path = write_json_artifact(
                    artifacts_dir / "trace_api_waterfall_raw.json",
                    raw_trace,
                )
                trace.raw_artifact = raw_path
                evidence.raw_artifacts.append(raw_path)
                evidence.direct_lookup = CapabilityAssessment(
                    "direct trace lookup",
                    CapabilityState.OBSERVED,
                    f"retrieved {len(trace.spans)} span(s)",
                )
                evidence.observations["direct_trace_lookup"] = "succeeded"
            except Exception as exc:
                evidence.direct_lookup = CapabilityAssessment(
                    "direct trace lookup",
                    CapabilityState.FAILED,
                    f"{exc.__class__.__name__}: {exc}",
                )
                record_probe_error(evidence, logger, config, "direct_trace_lookup", exc)
        else:
            evidence.direct_lookup = CapabilityAssessment(
                "direct trace lookup",
                CapabilityState.UNAVAILABLE,
                "SIGNOZ_API_KEY is unset; direct lookup was not attempted",
            )
    else:
        evidence.direct_lookup = CapabilityAssessment(
            "direct trace lookup",
            CapabilityState.NOT_CONFIGURED,
            "SIGNOZ_TRACE_ID is unset",
        )

    search_hits: list[TraceSearchHit] = []
    if config.agent_run_id:
        if config.signoz_api_key:
            try:
                search_hits, raw_search = client.find_trace_by_run_id(config.agent_run_id)
                raw_path = write_json_artifact(
                    artifacts_dir / "trace_api_search_raw.json",
                    raw_search,
                )
                evidence.raw_artifacts.append(raw_path)
                evidence.attribute_search = CapabilityAssessment(
                    "attribute-based trace search",
                    CapabilityState.OBSERVED,
                    f"agent.run_id matched {len(search_hits)} span row(s)",
                )
                evidence.observations["attribute_search"] = "succeeded"
            except EmptySearchResults as exc:
                evidence.attribute_search = CapabilityAssessment(
                    "attribute-based trace search",
                    CapabilityState.NOT_OBSERVED,
                    str(exc),
                )
                record_probe_error(evidence, logger, config, "attribute_search", exc)
            except Exception as exc:
                evidence.attribute_search = CapabilityAssessment(
                    "attribute-based trace search",
                    CapabilityState.FAILED,
                    f"{exc.__class__.__name__}: {exc}",
                )
                record_probe_error(evidence, logger, config, "attribute_search", exc)
        else:
            evidence.attribute_search = CapabilityAssessment(
                "attribute-based trace search",
                CapabilityState.UNAVAILABLE,
                "SIGNOZ_API_KEY is unset; attribute search was not attempted",
            )
    else:
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.NOT_CONFIGURED,
            "TRACEGUARD_AGENT_RUN_ID is unset",
        )

    if trace is None and search_hits:
        try:
            trace, raw_trace = client.get_trace(search_hits[0].trace_id)
            raw_path = write_json_artifact(
                artifacts_dir / "trace_api_waterfall_from_search_raw.json",
                raw_trace,
            )
            trace.raw_artifact = raw_path
            evidence.raw_artifacts.append(raw_path)
            evidence.observations["retrieval_by_search_result"] = "succeeded"
        except Exception as exc:
            record_probe_error(evidence, logger, config, "retrieval_by_search_result", exc)

    if trace is None:
        evidence.response_classification = "not observed"
        evidence.deterministic_evaluation = CapabilityAssessment(
            "suitable for deterministic evaluation",
            CapabilityState.NOT_OBSERVED,
            "no trace was retrieved",
        )
        evidence.error_behavior = CapabilityAssessment(
            "error behavior",
            CapabilityState.OBSERVED if evidence.errors else CapabilityState.NOT_OBSERVED,
            "operation errors are recorded independently" if evidence.errors else "",
        )
        return evidence

    evidence.trace = trace
    evidence.field_assessments = trace.field_assessments()
    evidence.response_classification = classify_trace_structure(trace)
    evidence.deterministic_evaluation = deterministic_assessment(trace)
    if trace.has_all_required_fields() and (
        evidence.attribute_search.state == CapabilityState.OBSERVED
        or not config.agent_run_id
    ) and (
        evidence.direct_lookup.state == CapabilityState.OBSERVED
        or evidence.observations.get("retrieval_by_search_result") == "succeeded"
    ):
        evidence.retrieval_workflow = CapabilityAssessment(
            "retrieval workflow completeness",
            CapabilityState.OBSERVED,
            "direct retrieval and configured agent.run_id discovery were observed",
        )
    elif trace.has_all_required_fields():
        evidence.retrieval_workflow = CapabilityAssessment(
            "retrieval workflow completeness",
            CapabilityState.NOT_OBSERVED,
            "direct retrieval is usable, but configured agent.run_id discovery was not fully observed",
        )
    else:
        evidence.retrieval_workflow = CapabilityAssessment(
            "retrieval workflow completeness",
            CapabilityState.FAILED,
            "retrieved trace did not contain all required fields",
        )
    evidence.human_explanation = CapabilityAssessment(
        "suitable for human explanation",
        CapabilityState.OBSERVED,
        "structured fields can be rendered or explained later",
    )
    evidence.preserves_multiple_spans, evidence.preserves_parent_child = (
        relationship_capabilities(trace)
    )
    evidence.error_behavior = CapabilityAssessment(
        "error behavior",
        CapabilityState.OBSERVED,
        "HTTP auth/not-found/schema failures map to custom exceptions and operation errors are retained",
    )
    evidence.response_stability = CapabilityAssessment(
        "response stability",
        CapabilityState.NOT_OBSERVED,
        "not tested by the Trace API probe",
    )
    normalized_path = write_json_artifact(
        artifacts_dir / "trace_api_normalized.json",
        trace.to_dict(),
    )
    evidence.raw_artifacts.append(normalized_path)

    return evidence


def record_probe_error(
    evidence: ProbeEvidence,
    logger: logging.Logger,
    config: Gate2Config,
    operation: str,
    exc: Exception,
) -> None:
    evidence.errors.append(f"{operation}: {exc.__class__.__name__}: {exc}")
    evidence.observations[operation] = f"failed: {exc.__class__.__name__}"
    logger.error(
        "trace_api_operation_failed",
        extra={
            "_source": Source.TRACE_API.value,
            "_operation": operation,
            "_error_category": exc.__class__.__name__,
        },
        exc_info=config.debug,
    )


def print_api_probe(evidence: ProbeEvidence) -> None:
    print(json.dumps(evidence.to_dict(), indent=2, sort_keys=True, default=str))


def main() -> int:
    config = Gate2Config.from_env()
    logger = configure_logging(config.debug)
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    evidence = run_trace_api_probe(config, logger, artifacts_dir)
    print_api_probe(evidence)
    return 0 if evidence.trace else 1


if __name__ == "__main__":
    sys.exit(main())
