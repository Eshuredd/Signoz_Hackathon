from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config import Gate2Config
from exceptions import (
    ConfigurationError,
    ConnectionFailure,
    IncompleteMCPTelemetry,
    InvalidResponseSchema,
    MCPToolUnavailable,
    MCPUnavailable,
    RequestTimeout,
)
from logging_config import configure_logging
from models import (
    CapabilityAssessment,
    CapabilityState,
    FieldAssessment,
    FieldState,
    ProbeEvidence,
    REQUIRED_FIELDS,
    Source,
    Span,
    Trace,
    classify_trace_structure,
    deterministic_assessment,
    now_utc,
    relationship_capabilities,
)


MCP_PROTOCOL_VERSION = "2025-06-18"


class MCPHttpClient:
    def __init__(self, config: Gate2Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.session = requests.Session()
        self.next_id = 1

    def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.session.get(
                self.config.mcp_health_url,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise RequestTimeout(f"MCP health check timed out: {self.config.mcp_health_url}") from exc
        except requests.ConnectionError as exc:
            raise MCPUnavailable(
                f"MCP health check failed: {self.config.mcp_health_url} is not reachable."
            ) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "mcp_health_checked",
            extra={
                "_source": Source.MCP.value,
                "_operation": "health",
                "_endpoint": self.config.mcp_health_url,
                "_status_code": response.status_code,
                "_elapsed_ms": elapsed_ms,
            },
        )

        if response.status_code >= 400:
            raise MCPUnavailable(
                f"MCP health check returned HTTP {response.status_code}."
            )
        return {
            "status_code": response.status_code,
            "body": response.text[:500],
            "headers": safe_headers(response.headers),
        }

    def initialize(self) -> dict[str, Any]:
        return self.json_rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "traceguard-gate2", "version": "0.1.0"},
            },
        )

    def initialized_notification(self) -> None:
        self.post_notification("notifications/initialized")

    def list_tools(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        response = self.json_rpc("tools/list")
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise InvalidResponseSchema("MCP tools/list response did not contain result.tools.")
        return result["tools"], response

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.json_rpc("tools/call", {"name": name, "arguments": arguments})

    def json_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        response = self._post(body)
        if "error" in response:
            raise InvalidResponseSchema(f"MCP JSON-RPC {method} returned error: {response['error']}")
        return response

    def post_notification(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self.config.signoz_api_key:
            headers["SIGNOZ-API-KEY"] = self.config.signoz_api_key

        try:
            response = self.session.post(
                self.config.mcp_url,
                headers=headers,
                json=body,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise RequestTimeout(f"MCP JSON-RPC call timed out: {body.get('method')}") from exc
        except requests.ConnectionError as exc:
            raise ConnectionFailure(f"Could not connect to MCP endpoint {self.config.mcp_url}.") from exc

        if response.status_code >= 400:
            raise MCPUnavailable(
                f"MCP endpoint returned HTTP {response.status_code} for {body.get('method')}."
            )
        return parse_mcp_response(response)


def parse_mcp_response(response: requests.Response) -> dict[str, Any]:
    content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        return parse_sse_json(response.text)
    try:
        payload = response.json()
    except ValueError as exc:
        raise InvalidResponseSchema("MCP response was not JSON or SSE JSON.") from exc
    if not isinstance(payload, dict):
        raise InvalidResponseSchema("MCP JSON-RPC response must be an object.")
    return payload


def parse_sse_json(text: str) -> dict[str, Any]:
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    for candidate in data_lines:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise InvalidResponseSchema("MCP SSE response did not contain a JSON data event.")


def safe_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    redacted = {}
    for key, value in headers.items():
        lowered = key.lower()
        if "key" in lowered or "token" in lowered or "authorization" in lowered:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def write_json_artifact(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    return str(path)


def run_mcp_probe(
    config: Gate2Config,
    logger: logging.Logger,
    artifacts_dir: Path,
) -> ProbeEvidence:
    evidence = ProbeEvidence(
        source=Source.MCP,
        available=False,
        non_secret_config=config.non_secret_snapshot(),
        commands_attempted=[
            f"curl -fsS {config.mcp_health_url}",
            "POST <SIGNOZ_MCP_URL> JSON-RPC initialize",
            "POST <SIGNOZ_MCP_URL> JSON-RPC tools/list",
            "POST <SIGNOZ_MCP_URL> JSON-RPC tools/call signoz_get_trace_details",
            "POST <SIGNOZ_MCP_URL> JSON-RPC tools/call signoz_search_traces",
        ],
    )
    evidence.installed_signoz_version = fetch_signoz_version(config)
    evidence.field_assessments = [
        FieldAssessment(field, FieldState.UNAVAILABLE, "MCP trace data not observed")
        for field in REQUIRED_FIELDS
    ]

    client = MCPHttpClient(config, logger)
    try:
        health = client.health()
        evidence.available = True
        evidence.raw_artifacts.append(
            write_json_artifact(artifacts_dir / "mcp_health.json", health)
        )

        init_response = client.initialize()
        evidence.raw_artifacts.append(
            write_json_artifact(artifacts_dir / "mcp_initialize_raw.json", init_response)
        )
        evidence.observations["initialize"] = "succeeded"
        client.initialized_notification()
        evidence.observations["notifications_initialized"] = "succeeded"

        tools, tools_response = client.list_tools()
        evidence.raw_artifacts.append(
            write_json_artifact(artifacts_dir / "mcp_tools_list_raw.json", tools_response)
        )
        tool_names = {str(tool.get("name")): tool for tool in tools if tool.get("name")}
        trace_related = sorted(
            name for name in tool_names if "trace" in name.lower()
        )
        evidence.observations["tools_list"] = "succeeded"
        evidence.observations["trace_related_tools"] = trace_related
        evidence.response_stability = CapabilityAssessment(
            "response stability",
            CapabilityState.NOT_OBSERVED,
            f"tools/list returned {len(tool_names)} tool(s); repeated trace retrieval not yet tested",
        )

        details_trace: Trace | None = None
        search_trace: Trace | None = None
        try:
            details_trace = try_mcp_direct_lookup(
                client,
                config,
                tool_names,
                evidence,
                artifacts_dir,
            )
        except Exception as exc:
            evidence.direct_lookup = CapabilityAssessment(
                "direct trace lookup",
                CapabilityState.FAILED,
                f"{exc.__class__.__name__}: {exc}",
            )
            evidence.errors.append(f"mcp_direct_trace_lookup: {exc.__class__.__name__}: {exc}")

        try:
            search_trace = try_mcp_attribute_search(
                client,
                config,
                tool_names,
                evidence,
                artifacts_dir,
            )
        except Exception as exc:
            evidence.attribute_search = CapabilityAssessment(
                "attribute-based trace search",
                CapabilityState.FAILED,
                f"{exc.__class__.__name__}: {exc}",
            )
            evidence.errors.append(f"mcp_attribute_search: {exc.__class__.__name__}: {exc}")

        trace = details_trace or search_trace

        if trace is None:
            raise IncompleteMCPTelemetry(
                "MCP was reachable, but no structured trace telemetry was normalized from tool output."
            )

        evidence.trace = trace
        evidence.field_assessments = trace.field_assessments()
        evidence.response_classification = classify_trace_structure(trace)
        evidence.deterministic_evaluation = deterministic_assessment(trace)
        evidence.human_explanation = CapabilityAssessment(
            "suitable for human explanation",
            CapabilityState.OBSERVED,
            "MCP returned trace-related content",
        )
        evidence.preserves_multiple_spans, evidence.preserves_parent_child = (
            relationship_capabilities(trace)
        )
        evidence.error_behavior = CapabilityAssessment(
            "error behavior",
            CapabilityState.OBSERVED,
            "MCP availability, tool, and schema failures map to custom exceptions",
        )
        normalized_path = write_json_artifact(
            artifacts_dir / "mcp_normalized.json",
            trace.to_dict(),
        )
        evidence.raw_artifacts.append(normalized_path)
    except Exception as exc:
        evidence.errors.append(f"{exc.__class__.__name__}: {exc}")
        evidence.blocker = infer_mcp_blocker(config)
        evidence.smallest_unblock = (
            "Enable or install the SigNoz MCP server for the local Foundry stack, "
            "expose its HTTP /mcp and /livez endpoints, and provide a service account API key."
        )
        evidence.direct_lookup = unavailable_if_not_set(evidence.direct_lookup, "MCP trace tool not reached")
        evidence.attribute_search = unavailable_if_not_set(
            evidence.attribute_search,
            "MCP trace search tool not reached",
        )
        evidence.authentication_required = CapabilityAssessment(
            "authentication required",
            CapabilityState.NOT_OBSERVED,
            "not runtime-observed because the MCP HTTP endpoint was unavailable",
        )
        evidence.error_behavior = CapabilityAssessment(
            "error behavior",
            CapabilityState.OBSERVED,
            f"{exc.__class__.__name__}: {exc}",
        )
        evidence.response_stability = CapabilityAssessment(
            "response stability",
            CapabilityState.UNAVAILABLE,
            "MCP server did not complete health/tools probes",
        )
        evidence.response_classification = "mcp unavailable"
        logger.error(
            "mcp_probe_failed",
            extra={
                "_source": Source.MCP.value,
                "_operation": "run_mcp_probe",
                "_endpoint": config.mcp_url,
                "_error_category": exc.__class__.__name__,
            },
            exc_info=config.debug,
        )

    return evidence


def unavailable_if_not_set(
    assessment: CapabilityAssessment,
    notes: str,
) -> CapabilityAssessment:
    if assessment.state == CapabilityState.NOT_OBSERVED:
        return CapabilityAssessment(assessment.capability, CapabilityState.UNAVAILABLE, notes)
    return assessment


def fetch_signoz_version(config: Gate2Config) -> str | None:
    try:
        response = requests.get(
            f"{config.signoz_base_url}/api/v1/version",
            timeout=config.request_timeout_seconds,
        )
        if response.status_code == 200:
            payload = response.json()
            return str(payload.get("version") or "unknown")
    except Exception:
        return None
    return None


def infer_mcp_blocker(config: Gate2Config) -> str:
    lock_path = Path(__file__).resolve().parent.parent / "casting.yaml.lock"
    if lock_path.exists():
        text = lock_path.read_text()
        mcp_section = ""
        if "\n  mcp:" in text:
            mcp_section = text.split("\n  mcp:", 1)[1].split("\n  metastore:", 1)[0]
        if "enabled: false" in mcp_section:
            return (
                "Local Foundry lock contains an MCP section with enabled: false; "
                f"{config.mcp_health_url} is not reachable."
            )
        if "enabled: true" in mcp_section:
            return (
                "Local Foundry lock contains MCP enabled, but the MCP endpoint is "
                f"not reachable at {config.mcp_health_url}."
            )
    return f"MCP endpoint {config.mcp_health_url} is not reachable."


def try_mcp_direct_lookup(
    client: MCPHttpClient,
    config: Gate2Config,
    tool_names: dict[str, dict[str, Any]],
    evidence: ProbeEvidence,
    artifacts_dir: Path,
) -> Trace | None:
    if not config.signoz_trace_id:
        evidence.direct_lookup = CapabilityAssessment(
            "direct trace lookup",
            CapabilityState.NOT_CONFIGURED,
            "SIGNOZ_TRACE_ID is unset",
        )
        return None

    tool = tool_names.get("signoz_get_trace_details")
    if tool is None:
        evidence.direct_lookup = CapabilityAssessment(
            "direct trace lookup",
            CapabilityState.UNAVAILABLE,
            "signoz_get_trace_details was not listed by MCP tools/list",
        )
        return None

    args = args_for_trace_details(tool, config.signoz_trace_id)
    raw_response = client.call_tool("signoz_get_trace_details", args)
    evidence.raw_artifacts.append(
        write_json_artifact(artifacts_dir / "mcp_get_trace_details_raw.json", raw_response)
    )
    trace = normalize_mcp_trace(raw_response)
    if trace is None:
        evidence.direct_lookup = CapabilityAssessment(
            "direct trace lookup",
            CapabilityState.FAILED,
            "tool returned no structured trace object",
        )
        return None

    evidence.direct_lookup = CapabilityAssessment(
        "direct trace lookup",
        CapabilityState.OBSERVED,
        f"normalized {len(trace.spans)} span(s)",
    )
    observe_mcp_stability(
        client,
        "signoz_get_trace_details",
        args,
        trace,
        evidence,
        artifacts_dir / "mcp_get_trace_details_repeat_raw.json",
    )
    return trace


def try_mcp_attribute_search(
    client: MCPHttpClient,
    config: Gate2Config,
    tool_names: dict[str, dict[str, Any]],
    evidence: ProbeEvidence,
    artifacts_dir: Path,
) -> Trace | None:
    if not config.agent_run_id:
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.NOT_CONFIGURED,
            "TRACEGUARD_AGENT_RUN_ID is unset",
        )
        return None

    tool = tool_names.get("signoz_search_traces")
    if tool is None:
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.UNAVAILABLE,
            "signoz_search_traces was not listed by MCP tools/list",
        )
        return None

    args = args_for_trace_search(tool, config.agent_run_id)
    raw_response = client.call_tool("signoz_search_traces", args)
    evidence.raw_artifacts.append(
        write_json_artifact(artifacts_dir / "mcp_search_traces_raw.json", raw_response)
    )
    trace = normalize_mcp_trace(raw_response)
    if trace is None:
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.FAILED,
            "tool returned no structured trace object",
        )
        return None

    evidence.attribute_search = CapabilityAssessment(
        "attribute-based trace search",
        CapabilityState.OBSERVED,
        f"normalized {len(trace.spans)} span(s)",
    )
    observe_mcp_stability(
        client,
        "signoz_search_traces",
        args,
        trace,
        evidence,
        artifacts_dir / "mcp_search_traces_repeat_raw.json",
    )
    return trace


def observe_mcp_stability(
    client: MCPHttpClient,
    tool_name: str,
    args: dict[str, Any],
    first_trace: Trace,
    evidence: ProbeEvidence,
    artifact_path: Path,
) -> None:
    try:
        repeat_response = client.call_tool(tool_name, args)
        evidence.raw_artifacts.append(write_json_artifact(artifact_path, repeat_response))
        repeat_trace = normalize_mcp_trace(repeat_response)
        if repeat_trace is None:
            evidence.response_stability = CapabilityAssessment(
                "response stability",
                CapabilityState.FAILED,
                "repeated tool call returned no structured trace",
            )
            return
        if structural_signature(first_trace) == structural_signature(repeat_trace):
            evidence.response_stability = CapabilityAssessment(
                "response stability",
                CapabilityState.OBSERVED,
                f"{tool_name} returned stable structural fields across two equivalent calls",
            )
            evidence.observations["stable_repeated_workflow"] = tool_name
        else:
            evidence.response_stability = CapabilityAssessment(
                "response stability",
                CapabilityState.FAILED,
                "repeated tool call changed normalized structural fields",
            )
    except Exception as exc:
        evidence.response_stability = CapabilityAssessment(
            "response stability",
            CapabilityState.FAILED,
            f"{exc.__class__.__name__}: {exc}",
        )
        evidence.errors.append(f"mcp_response_stability: {exc.__class__.__name__}: {exc}")


def structural_signature(trace: Trace) -> dict[str, Any]:
    return {
        "span_count": len(trace.spans),
        "field_states": {
            assessment.field: assessment.state.value
            for assessment in trace.field_assessments()
        },
        "span_shapes": [
            {
                "trace_id": bool(span.trace_id),
                "span_id": bool(span.span_id),
                "parent_span_id": span.parent_span_id is not None,
                "span_name": bool(span.span_name),
                "start_time": span.start_time is not None,
                "end_time": span.end_time is not None,
                "duration_nano": span.duration_nano is not None,
                "status": bool(span.status),
                "attribute_keys": sorted(span.attributes),
                "resource_attribute_keys": sorted(span.resource_attributes),
            }
            for span in trace.spans
        ],
    }


def args_for_trace_details(tool: dict[str, Any], trace_id: str) -> dict[str, Any]:
    properties = input_schema_properties(tool)
    for key in ("trace_id", "traceId", "traceID", "id"):
        if key in properties:
            return {key: trace_id}
    if properties:
        raise MCPToolUnavailable(
            "signoz_get_trace_details schema did not expose an obvious trace ID parameter."
        )
    return {"trace_id": trace_id}


def args_for_trace_search(tool: dict[str, Any], run_id: str) -> dict[str, Any]:
    properties = input_schema_properties(tool)
    expression = f"agent.run_id = '{run_id}'"
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(hours=24)).timestamp() * 1000)
    args: dict[str, Any] = {}

    for key in ("filter", "filter_expression", "filterExpression", "query"):
        if key in properties:
            args[key] = expression
            break
    for key in ("start", "startTime", "start_time"):
        if key in properties:
            args[key] = start_ms
            break
    for key in ("end", "endTime", "end_time"):
        if key in properties:
            args[key] = now_ms
            break
    for key in ("limit", "pageSize"):
        if key in properties:
            args[key] = 10
            break

    if not args:
        raise MCPToolUnavailable(
            "signoz_search_traces schema did not expose an obvious filter/query parameter."
        )
    return args


def input_schema_properties(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def normalize_mcp_trace(raw_response: dict[str, Any]) -> Trace | None:
    candidate = extract_structured_payload(raw_response)
    if candidate is None:
        return None
    trace_object = find_trace_object(candidate)
    if trace_object is None:
        return None
    return trace_from_mcp_object(trace_object)


def extract_structured_payload(raw_response: dict[str, Any]) -> Any:
    result = raw_response.get("result", raw_response)
    if isinstance(result, dict):
        structured = result.get("structuredContent") or result.get("structured_content")
        if structured is not None:
            return structured
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "json" and "json" in item:
                    return item["json"]
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        parsed = json.loads(item["text"])
                    except json.JSONDecodeError:
                        continue
                    return parsed
    return result


def find_trace_object(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("spans"), list):
            return payload
        for value in payload.values():
            found = find_trace_object(value)
            if found is not None:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = find_trace_object(value)
            if found is not None:
                return found
    return None


def trace_from_mcp_object(obj: dict[str, Any]) -> Trace:
    spans_raw = obj.get("spans")
    if not isinstance(spans_raw, list) or not spans_raw:
        raise IncompleteMCPTelemetry("MCP trace object had no spans list.")
    spans = [span_from_mcp_object(span) for span in spans_raw if isinstance(span, dict)]
    if not spans:
        raise IncompleteMCPTelemetry("MCP trace object contained no object spans.")
    trace_id = str(obj.get("trace_id") or obj.get("traceId") or spans[0].trace_id)
    return Trace(
        trace_id=trace_id,
        spans=spans,
        retrieved_at=now_utc(),
        source=Source.MCP,
        metadata={"normalization": "direct structured MCP payload only"},
    )


def span_from_mcp_object(raw: dict[str, Any]) -> Span:
    trace_id = value_for(raw, "trace_id", "traceId", "traceID")
    span_id = value_for(raw, "span_id", "spanId", "spanID")
    parent_span_id = value_for(raw, "parent_span_id", "parentSpanId", "parentSpanID")
    span_name = value_for(raw, "span_name", "spanName", "name")
    start = parse_timestamp(value_for(raw, "start_time", "startTime", "time_unix", "timestamp"))
    end = parse_timestamp(value_for(raw, "end_time", "endTime"))
    duration = parse_int(value_for(raw, "duration_nano", "durationNano", "duration"))
    if end is None and start is not None and duration is not None:
        end = start + timedelta(microseconds=duration / 1000)

    status = ensure_dict(raw.get("status"))
    if not status:
        status = {
            key: raw.get(key)
            for key in ("status_code", "statusCode", "status_code_string", "statusCodeString", "has_error", "hasError")
            if raw.get(key) is not None
        }

    resource = ensure_dict(
        raw.get("resource_attributes")
        or raw.get("resourceAttributes")
        or raw.get("resource")
    )
    attributes = ensure_dict(raw.get("attributes"))
    service_name = resource.get("service.name") or raw.get("service_name") or raw.get("serviceName")

    return Span(
        trace_id=str(trace_id or ""),
        span_id=str(span_id or ""),
        parent_span_id=str(parent_span_id) if parent_span_id is not None else None,
        span_name=str(span_name or ""),
        start_time=start,
        end_time=end,
        duration_nano=duration,
        status=status,
        attributes=attributes,
        resource_attributes=resource,
        service_name=str(service_name) if service_name else None,
        raw=raw,
    )


def value_for(raw: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return None


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


def classify_trace(trace: Trace) -> str:
    return classify_trace_structure(trace)


def main() -> int:
    config = Gate2Config.from_env()
    logger = configure_logging(config.debug)
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    evidence = run_mcp_probe(config, logger, artifacts_dir)
    print(json.dumps(evidence.to_dict(), indent=2, sort_keys=True, default=str))
    return 0 if evidence.trace else 1


if __name__ == "__main__":
    sys.exit(main())
