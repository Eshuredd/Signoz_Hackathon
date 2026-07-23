from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass
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
SESSION_HEADER = "Mcp-Session-Id"
TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
DETAILS_TOOL_NAMES = (
    "signoz_get_trace_details",
    "get_trace_details",
)
SEARCH_TOOL_NAMES = (
    "signoz_search_traces",
    "search_traces",
)
TRACE_ID_KEYS = ("trace_id", "traceId", "traceID")
RUN_ID_KEYS = ("agent.run_id", "agentRunId", "agent_run_id")


@dataclass(frozen=True)
class MCPTraceTools:
    details: dict[str, Any]
    search: dict[str, Any] | None

    @property
    def details_name(self) -> str:
        return str(self.details["name"])

    @property
    def search_name(self) -> str | None:
        if self.search is None:
            return None
        return str(self.search["name"])


@dataclass(frozen=True)
class MCPTraceSearchHit:
    trace_id: str
    attributes: dict[str, Any]
    raw: dict[str, Any]


class MCPHttpClient:
    def __init__(self, config: Gate2Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.session = requests.Session()
        self.next_id = 1
        self.session_id: str | None = None

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
        response = self._post_request(body)
        if "error" in response:
            raise InvalidResponseSchema(f"MCP JSON-RPC {method} returned error: {response['error']}")
        return response

    def post_notification(self, method: str) -> dict[str, Any] | None:
        return self._post_notification({"jsonrpc": "2.0", "method": method})

    def _headers_for(self, body: dict[str, Any]) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": str(body.get("method") or ""),
        }
        params = body.get("params")
        if isinstance(params, dict) and isinstance(params.get("name"), str):
            headers["Mcp-Name"] = params["name"]
        if self.config.signoz_api_key:
            headers["SIGNOZ-API-KEY"] = self.config.signoz_api_key
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return headers

    def _post_request(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self._send_post(body)
        self._capture_session_id(response)
        if response.status_code >= 400:
            raise MCPUnavailable(
                f"MCP endpoint returned HTTP {response.status_code} for {body.get('method')}: "
                f"{response.text[:500]}"
            )
        if response.status_code == 202 and not response.text.strip():
            raise InvalidResponseSchema(
                f"MCP JSON-RPC request {body.get('method')} returned HTTP 202 with no response body."
            )
        return parse_mcp_response(response)

    def _post_notification(self, body: dict[str, Any]) -> dict[str, Any] | None:
        response = self._send_post(body)
        self._capture_session_id(response)
        if response.status_code >= 400:
            raise MCPUnavailable(
                f"MCP notification {body.get('method')} returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        if response.status_code == 202 and not response.text.strip():
            return None
        if not response.text.strip():
            return None
        return parse_mcp_response(response)

    def _send_post(self, body: dict[str, Any]) -> requests.Response:
        try:
            response = self.session.post(
                self.config.mcp_url,
                headers=self._headers_for(body),
                json=body,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise RequestTimeout(f"MCP JSON-RPC call timed out: {body.get('method')}") from exc
        except requests.ConnectionError as exc:
            raise ConnectionFailure(f"Could not connect to MCP endpoint {self.config.mcp_url}.") from exc

        return response

    def _capture_session_id(self, response: requests.Response) -> None:
        value = response.headers.get(SESSION_HEADER)
        if value:
            self.session_id = value


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
    for event_data in sse_event_data(text):
        if not event_data.strip():
            continue
        try:
            parsed = json.loads(event_data)
        except json.JSONDecodeError:
            continue
        if is_json_rpc_object(parsed):
            return parsed
    raise InvalidResponseSchema("MCP SSE response did not contain a valid JSON-RPC object.")


def sse_event_data(text: str) -> list[str]:
    events: list[str] = []
    data_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        if field == "data":
            data_lines.append(value)
    if data_lines:
        events.append("\n".join(data_lines))
    return events


def is_json_rpc_object(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("jsonrpc") == "2.0"
        and any(key in value for key in ("id", "result", "error", "method"))
    )


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
        evidence.failed_stage = "mcp_health"
        health = client.health()
        evidence.available = True
        evidence.failed_stage = None
        evidence.observations["mcp_health"] = "succeeded"
        evidence.raw_artifacts.append(
            write_json_artifact(artifacts_dir / "mcp_health.json", health)
        )

        evidence.failed_stage = "mcp_initialize"
        init_response = client.initialize()
        evidence.raw_artifacts.append(
            write_json_artifact(artifacts_dir / "mcp_initialize_raw.json", init_response)
        )
        evidence.observations["initialize"] = "succeeded"
        evidence.observations["session_id_issued"] = client.session_id is not None
        evidence.failed_stage = "mcp_initialized_notification"
        notification_response = client.initialized_notification()
        evidence.observations["notifications_initialized"] = "succeeded"
        if notification_response is not None:
            evidence.observations["notifications_initialized_response"] = "non-empty"

        evidence.failed_stage = "mcp_tools_list"
        tools, tools_response = client.list_tools()
        evidence.raw_artifacts.append(
            write_json_artifact(artifacts_dir / "mcp_tools_list_raw.json", tools_response)
        )
        tool_names = {str(tool.get("name")): tool for tool in tools if tool.get("name")}
        trace_related = sorted(
            name for name in tool_names if "trace" in name.lower()
        )
        evidence.observations["tools_list"] = "succeeded"
        evidence.observations["actual_tool_names"] = sorted(tool_names)
        evidence.observations["trace_related_tools"] = trace_related
        evidence.observations["tool_input_schema_summaries"] = [
            tool_schema_summary(tool) for tool in tools if isinstance(tool, dict)
        ]
        evidence.failed_stage = "mcp_tool_discovery"
        trace_tools = discover_mcp_trace_tools(tools)
        evidence.observations["selected_trace_details_tool"] = trace_tools.details_name
        evidence.observations["selected_trace_search_tool"] = trace_tools.search_name
        evidence.failed_stage = None
        evidence.response_stability = CapabilityAssessment(
            "response stability",
            CapabilityState.NOT_OBSERVED,
            f"tools/list returned {len(tool_names)} tool(s); repeated trace retrieval not yet tested",
        )

        details_trace: Trace | None = None
        search_trace: Trace | None = None
        failed_workflow_stages: list[str] = []
        try:
            evidence.failed_stage = "mcp_direct_lookup"
            details_trace = try_mcp_direct_lookup(
                client,
                config,
                trace_tools,
                evidence,
                artifacts_dir,
            )
            if details_trace is not None:
                evidence.observations["mcp_direct_lookup"] = "succeeded"
        except Exception as exc:
            failed_workflow_stages.append("mcp_direct_lookup")
            evidence.direct_lookup = CapabilityAssessment(
                "direct trace lookup",
                CapabilityState.FAILED,
                f"{exc.__class__.__name__}: {exc}",
            )
            evidence.errors.append(f"mcp_direct_trace_lookup: {exc.__class__.__name__}: {exc}")

        try:
            evidence.failed_stage = "mcp_attribute_search"
            search_trace = try_mcp_attribute_search(
                client,
                config,
                trace_tools,
                evidence,
                artifacts_dir,
            )
            if search_trace is not None:
                evidence.observations["mcp_attribute_search_to_details"] = "succeeded"
        except Exception as exc:
            failed_workflow_stages.append("mcp_attribute_search")
            evidence.attribute_search = CapabilityAssessment(
                "attribute-based trace search",
                CapabilityState.FAILED,
                f"{exc.__class__.__name__}: {exc}",
            )
            evidence.errors.append(f"mcp_attribute_search: {exc.__class__.__name__}: {exc}")

        trace = details_trace or search_trace

        if trace is None:
            evidence.failed_stage = failed_workflow_stages[-1] if failed_workflow_stages else "mcp_normalization"
            evidence.response_classification = "not observed"
            evidence.error_behavior = CapabilityAssessment(
                "error behavior",
                CapabilityState.OBSERVED,
                "MCP health/initialize/tools completed, but no trace workflow normalized telemetry",
            )
            evidence.retrieval_workflow = CapabilityAssessment(
                "retrieval workflow completeness",
                CapabilityState.FAILED,
                "no MCP direct or search-to-details workflow returned a normalized trace",
            )
            return evidence

        evidence.trace = trace
        evidence.failed_stage = "mcp_normalization"
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
        evidence.failed_stage = "mcp_relationship_validation"
        if (
            evidence.direct_lookup.state == CapabilityState.OBSERVED
            or evidence.attribute_search.state == CapabilityState.OBSERVED
        ):
            evidence.retrieval_workflow = CapabilityAssessment(
                "retrieval workflow completeness",
                CapabilityState.OBSERVED,
                "at least one MCP trace retrieval workflow normalized structured telemetry",
            )
        evidence.error_behavior = CapabilityAssessment(
            "error behavior",
            CapabilityState.OBSERVED,
            "MCP availability, tool, and schema failures map to custom exceptions",
        )
        if evidence.response_stability.state != CapabilityState.FAILED:
            evidence.failed_stage = None
        normalized_path = write_json_artifact(
            artifacts_dir / "mcp_normalized.json",
            trace.to_dict(),
        )
        evidence.raw_artifacts.append(normalized_path)
    except Exception as exc:
        stage = evidence.failed_stage or "mcp_unknown"
        evidence.errors.append(f"{stage}: {exc.__class__.__name__}: {exc}")
        evidence.blocker = infer_mcp_blocker(config, stage, exc)
        evidence.smallest_unblock = (
            smallest_unblock_for_stage(config, stage)
        )
        evidence.direct_lookup = unavailable_if_not_set(evidence.direct_lookup, "MCP trace tool not reached")
        evidence.attribute_search = unavailable_if_not_set(
            evidence.attribute_search,
            "MCP trace search tool not reached",
        )
        error_text = str(exc)
        if "401" in error_text or "SIGNOZ-API-KEY" in error_text or "Authorization" in error_text:
            evidence.authentication_required = CapabilityAssessment(
                "authentication required",
                CapabilityState.OBSERVED,
                f"{stage} returned an authentication error",
            )
        else:
            evidence.authentication_required = CapabilityAssessment(
                "authentication required",
                CapabilityState.NOT_OBSERVED,
                f"not observed before failure stage {stage}",
            )
        evidence.error_behavior = CapabilityAssessment(
            "error behavior",
            CapabilityState.OBSERVED,
            f"{exc.__class__.__name__}: {exc}",
        )
        evidence.response_stability = CapabilityAssessment(
            "response stability",
            CapabilityState.UNAVAILABLE,
            f"MCP probe failed before stability check at {stage}",
        )
        evidence.response_classification = "mcp unavailable" if not evidence.available else "not observed"
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


def infer_mcp_blocker(config: Gate2Config, stage: str, exc: Exception) -> str | None:
    if stage not in {
        "mcp_health",
        "mcp_initialize",
        "mcp_initialized_notification",
        "mcp_tools_list",
        "mcp_tool_discovery",
    }:
        return None
    if isinstance(exc, IncompleteMCPTelemetry):
        return None
    if stage in {"mcp_initialize", "mcp_initialized_notification", "mcp_tools_list"}:
        return f"MCP endpoint was reachable, but {stage} failed: {exc.__class__.__name__}: {exc}"
    if stage == "mcp_tool_discovery":
        return f"MCP tools/list succeeded, but no compatible trace workflow could be discovered: {exc}"
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


def smallest_unblock_for_stage(config: Gate2Config, stage: str) -> str:
    if stage == "mcp_health":
        return (
            "Start Docker/SigNoz MCP, verify "
            f"{config.mcp_health_url}, then rerun gate2/mcp_probe.py."
        )
    if stage in {"mcp_initialize", "mcp_initialized_notification", "mcp_tools_list"}:
        return (
            "Inspect the MCP server logs, protocol/session behavior, and service-account "
            "authentication, then rerun gate2/mcp_probe.py."
        )
    if stage == "mcp_tool_discovery":
        return "Inspect tools/list and confirm a compatible trace details tool is exposed."
    return "Inspect the recorded MCP evidence for the failed workflow stage and rerun the probe."


def try_mcp_direct_lookup(
    client: MCPHttpClient,
    config: Gate2Config,
    trace_tools: MCPTraceTools,
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

    tool = trace_tools.details
    tool_name = trace_tools.details_name
    args = args_for_trace_details(tool, config.signoz_trace_id)
    evidence.failed_stage = "mcp_direct_lookup"
    raw_response = client.call_tool(tool_name, args)
    evidence.raw_artifacts.append(
        write_json_artifact(artifacts_dir / "mcp_get_trace_details_raw.json", raw_response)
    )
    evidence.failed_stage = "mcp_normalization"
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
        tool_name,
        args,
        trace,
        evidence,
        artifacts_dir / "mcp_get_trace_details_repeat_raw.json",
    )
    return trace


def try_mcp_attribute_search(
    client: MCPHttpClient,
    config: Gate2Config,
    trace_tools: MCPTraceTools,
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

    search_tool = trace_tools.search
    if search_tool is None:
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.UNAVAILABLE,
            "no compatible trace search tool was discovered from tools/list",
        )
        return None

    search_name = trace_tools.search_name
    if search_name is None:
        raise MCPToolUnavailable("Compatible trace search tool had no name.")
    args = args_for_trace_search(search_tool, config.agent_run_id)
    evidence.failed_stage = "mcp_attribute_search"
    raw_response = client.call_tool(search_name, args)
    evidence.raw_artifacts.append(
        write_json_artifact(artifacts_dir / "mcp_search_traces_raw.json", raw_response)
    )
    evidence.failed_stage = "mcp_search_result_parsing"
    hits = extract_trace_search_hits(raw_response)
    evidence.observations["mcp_search_hit_count"] = len(hits)
    selected = select_trace_id_for_run_id(hits, config.agent_run_id)
    evidence.observations["mcp_search_selected_trace_id"] = "<observed>"
    details_args = args_for_trace_details(trace_tools.details, selected)
    evidence.failed_stage = "mcp_details_from_search"
    details_response = client.call_tool(trace_tools.details_name, details_args)
    evidence.raw_artifacts.append(
        write_json_artifact(
            artifacts_dir / "mcp_get_trace_details_from_search_raw.json",
            details_response,
        )
    )
    evidence.failed_stage = "mcp_normalization"
    trace = normalize_mcp_trace(details_response)
    if trace is None:
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.FAILED,
            "search succeeded, but details response returned no structured trace object",
        )
        return None
    if not trace_contains_run_id(trace, config.agent_run_id):
        evidence.attribute_search = CapabilityAssessment(
            "attribute-based trace search",
            CapabilityState.FAILED,
            "search-to-details trace did not contain requested agent.run_id in available attributes",
        )
        return None

    evidence.attribute_search = CapabilityAssessment(
        "attribute-based trace search",
        CapabilityState.OBSERVED,
        f"search selected a trace ID and details normalized {len(trace.spans)} span(s)",
    )
    observe_mcp_stability(
        client,
        trace_tools.details_name,
        details_args,
        trace,
        evidence,
        artifacts_dir / "mcp_get_trace_details_from_search_repeat_raw.json",
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
        evidence.failed_stage = "mcp_stability_check"
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
        "trace_id": trace.trace_id,
        "span_count": len(trace.spans),
        "field_states": {
            assessment.field: assessment.state.value
            for assessment in trace.field_assessments()
        },
        "spans": sorted(
            [
            {
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "span_name": span.span_name,
                "attribute_keys": sorted(span.attributes),
                "resource_attribute_keys": sorted(span.resource_attributes),
            }
            for span in trace.spans
            ],
            key=lambda item: item["span_id"],
        ),
    }


def discover_mcp_trace_tools(tools: list[dict[str, Any]]) -> MCPTraceTools:
    named = {str(tool.get("name")): tool for tool in tools if isinstance(tool.get("name"), str)}
    details = first_compatible_tool(named, DETAILS_TOOL_NAMES, "details")
    search = first_compatible_tool(named, SEARCH_TOOL_NAMES, "search")
    if details is None:
        raise MCPToolUnavailable(
            "MCP tools/list did not expose a compatible trace details tool."
        )
    return MCPTraceTools(details=details, search=search)


def first_compatible_tool(
    named: dict[str, dict[str, Any]],
    candidates: tuple[str, ...],
    role: str,
) -> dict[str, Any] | None:
    for name in candidates:
        tool = named.get(name)
        if tool is None:
            continue
        try:
            if role == "details":
                args_for_trace_details(tool, "0" * 32)
            else:
                args_for_trace_search(tool, "run-id")
        except MCPToolUnavailable:
            continue
        return tool
    return None


def tool_schema_summary(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    properties = input_schema_properties(tool)
    required = schema.get("required") if isinstance(schema, dict) else None
    return {
        "name": tool.get("name"),
        "property_names": sorted(properties),
        "required": required if isinstance(required, list) else [],
    }


def args_for_trace_details(tool: dict[str, Any], trace_id: str) -> dict[str, Any]:
    properties = input_schema_properties(tool)
    required = input_schema_required(tool)
    unsupported_required = [name for name in required if name not in TRACE_ID_KEYS]
    if unsupported_required:
        raise MCPToolUnavailable(
            "trace details schema has required fields that Gate 2 cannot map safely: "
            + ", ".join(unsupported_required)
        )
    for key in TRACE_ID_KEYS:
        if key in properties:
            return {key: trace_id}
    raise MCPToolUnavailable(
        "trace details schema did not expose a supported trace ID parameter."
    )


def args_for_trace_search(tool: dict[str, Any], run_id: str) -> dict[str, Any]:
    properties = input_schema_properties(tool)
    required = input_schema_required(tool)
    expression = f"agent.run_id = '{run_id}'"
    args: dict[str, Any] = {}

    search_keys = ("filter", "filter_expression", "filterExpression", "query", "q")
    for key in search_keys:
        if key in properties:
            args[key] = expression
            break
    for key in ("limit", "pageSize"):
        if key in properties:
            args[key] = 10
            break

    if not any(key in args for key in search_keys):
        raise MCPToolUnavailable(
            "trace search schema did not expose a supported filter/query parameter."
        )
    unsupported_required = [
        name
        for name in required
        if name not in args
        and name not in search_keys
        and name not in {"limit", "pageSize"}
    ]
    if unsupported_required:
        raise MCPToolUnavailable(
            "trace search schema has required fields that Gate 2 cannot map safely: "
            + ", ".join(unsupported_required)
        )
    return args


def input_schema_properties(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def input_schema_required(tool: dict[str, Any]) -> list[str]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    return [str(item) for item in required] if isinstance(required, list) else []


def extract_trace_search_hits(raw_response: dict[str, Any]) -> list[MCPTraceSearchHit]:
    payload = extract_structured_payload(raw_response)
    if payload is None:
        raise IncompleteMCPTelemetry("MCP search response contained no structured payload.")
    hits: list[MCPTraceSearchHit] = []
    for obj in iter_structured_objects(payload):
        trace_id = first_string_value(obj, TRACE_ID_KEYS)
        if trace_id is None:
            continue
        if not TRACE_ID_RE.fullmatch(trace_id):
            continue
        attrs = ensure_dict(
            obj.get("attributes")
            or obj.get("span_attributes")
            or obj.get("spanAttributes")
        )
        hits.append(MCPTraceSearchHit(trace_id=trace_id.lower(), attributes=attrs, raw=obj))
    if not hits:
        raise IncompleteMCPTelemetry(
            "MCP search response did not contain structured trace_id fields."
        )
    return hits


def iter_structured_objects(payload: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        objects.append(payload)
        for value in payload.values():
            if isinstance(value, (dict, list)):
                objects.extend(iter_structured_objects(value))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (dict, list)):
                objects.extend(iter_structured_objects(item))
    return objects


def first_string_value(obj: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def select_trace_id_for_run_id(hits: list[MCPTraceSearchHit], run_id: str) -> str:
    for hit in hits:
        if attributes_contain_run_id(hit.attributes, run_id):
            return hit.trace_id
    for hit in hits:
        attrs = ensure_dict(hit.raw.get("data")).get("attributes")
        if attributes_contain_run_id(ensure_dict(attrs), run_id):
            return hit.trace_id
    if len(hits) == 1 and not hit_has_any_run_id(hits[0]):
        return hits[0].trace_id
    raise IncompleteMCPTelemetry(
        "MCP search returned trace IDs, but none could be validated against requested agent.run_id."
    )


def hit_has_any_run_id(hit: MCPTraceSearchHit) -> bool:
    return any(key in hit.attributes for key in RUN_ID_KEYS)


def attributes_contain_run_id(attributes: dict[str, Any], run_id: str) -> bool:
    return any(str(attributes.get(key)) == run_id for key in RUN_ID_KEYS)


def trace_contains_run_id(trace: Trace, run_id: str) -> bool:
    attribute_maps = [span.attributes for span in trace.spans if span.attributes]
    if not attribute_maps:
        return True
    return any(attributes_contain_run_id(attrs, run_id) for attrs in attribute_maps)


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
