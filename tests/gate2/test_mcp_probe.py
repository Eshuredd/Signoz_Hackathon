from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from config import Gate2Config
from exceptions import IncompleteMCPTelemetry, InvalidResponseSchema, MCPToolUnavailable, MCPUnavailable
from mcp_probe import (
    MCPHttpClient,
    MCPTraceTools,
    args_for_trace_details,
    args_for_trace_search,
    discover_mcp_trace_tools,
    extract_trace_search_hits,
    normalize_mcp_trace,
    parse_sse_json,
    run_mcp_probe,
    select_trace_id_for_run_id,
    structural_signature,
    try_mcp_attribute_search,
)
from models import CapabilityState, ProbeEvidence, Source, Trace


class DummyLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        pass

    def error(self, *args: object, **kwargs: object) -> None:
        pass


def config() -> Gate2Config:
    return Gate2Config(
        signoz_base_url="http://localhost:8080",
        signoz_trace_id="a" * 32,
        agent_run_id="run-1",
        signoz_api_key="secret",
        request_timeout_seconds=1.0,
        debug=False,
        mcp_url="http://localhost:8000/mcp",
        mcp_health_url="http://localhost:8000/livez",
    )


def response(
    status: int = 200,
    payload: dict[str, Any] | None = None,
    *,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    item = requests.Response()
    item.status_code = status
    if text is not None:
        item._content = text.encode()
    else:
        item._content = json.dumps(payload if payload is not None else {}).encode()
    item.headers.update(headers or {"Content-Type": "application/json"})
    return item


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> requests.Response:
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def complete_span(parent_span_id: str = "") -> dict[str, object]:
    return {
        "trace_id": "a" * 32,
        "span_id": "root" if not parent_span_id else "child",
        "parent_span_id": parent_span_id,
        "name": "gate2.test.root" if not parent_span_id else "gate2.test.child",
        "start_time": "2026-01-01T00:00:00Z",
        "duration_nano": 1000,
        "status": {"code": "OK"},
        "attributes": {
            "agent.run_id": "run-1",
            "traceguard.run_id": "run-1",
            "traceguard.project": "TraceGuard",
            "traceguard.gate": "1A",
            "unknown": "kept",
        },
        "resource_attributes": {"service.name": "svc"},
    }


def test_structured_content_payload_normalizes_trace() -> None:
    raw = {"result": {"structuredContent": {"trace": {"spans": [complete_span()]}}}}

    trace = normalize_mcp_trace(raw)

    assert trace is not None
    assert trace.spans[0].attributes["unknown"] == "kept"


def test_json_content_payload_normalizes_trace() -> None:
    raw = {"result": {"content": [{"type": "json", "json": {"spans": [complete_span()]}}]}}

    assert normalize_mcp_trace(raw) is not None


def test_json_text_content_payload_normalizes_trace() -> None:
    raw = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": '{"spans": [{"trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "span_id": "root", "parent_span_id": "", "name": "root"}]}',
                }
            ]
        }
    }

    trace = normalize_mcp_trace(raw)

    assert trace is not None
    assert trace.spans[0].span_id == "root"


def test_natural_language_only_payload_returns_no_trace() -> None:
    raw = {"result": {"content": [{"type": "text", "text": "Here is your trace summary."}]}}

    assert normalize_mcp_trace(raw) is None


def test_multiple_spans_and_parent_child_preserved() -> None:
    raw = {"result": {"structuredContent": {"spans": [complete_span(), complete_span("root")]}}}

    trace = normalize_mcp_trace(raw)

    assert trace is not None
    assert len(trace.spans) == 2
    assert trace.has_valid_parent_child_relationship()


def test_tool_arguments_derive_from_input_schema() -> None:
    details_tool = {"inputSchema": {"properties": {"traceId": {"type": "string"}}}}
    search_tool = {
        "inputSchema": {
            "properties": {
                "filterExpression": {"type": "string"},
                "start": {"type": "number"},
                "end": {"type": "number"},
                "limit": {"type": "number"},
            }
        }
    }

    assert args_for_trace_details(details_tool, "abc") == {"traceId": "abc"}
    search_args = args_for_trace_search(search_tool, "run-1")
    assert search_args["filterExpression"] == "agent.run_id = 'run-1'"
    assert search_args["limit"] == 10


def test_initialize_captures_session_id_and_reuses_it() -> None:
    client = MCPHttpClient(config(), DummyLogger())
    client.session = FakeSession(
        [
            response(
                payload={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "session-secret"},
            ),
            response(payload={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
            response(payload={"jsonrpc": "2.0", "id": 3, "result": {}}),
            response(status=202, text="", headers={}),
        ]
    )

    client.initialize()
    client.list_tools()
    client.call_tool("signoz_get_trace_details", {"trace_id": "a" * 32})
    client.initialized_notification()

    fake = client.session
    assert isinstance(fake, FakeSession)
    assert client.session_id == "session-secret"
    assert "Mcp-Session-Id" not in fake.requests[0]["headers"]
    assert fake.requests[1]["headers"]["Mcp-Session-Id"] == "session-secret"
    assert fake.requests[2]["headers"]["Mcp-Session-Id"] == "session-secret"
    assert fake.requests[3]["headers"]["Mcp-Session-Id"] == "session-secret"


def test_no_session_header_when_server_does_not_provide_one() -> None:
    client = MCPHttpClient(config(), DummyLogger())
    client.session = FakeSession(
        [
            response(payload={"jsonrpc": "2.0", "id": 1, "result": {}}),
            response(payload={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
        ]
    )

    client.initialize()
    client.list_tools()

    fake = client.session
    assert isinstance(fake, FakeSession)
    assert client.session_id is None
    assert "Mcp-Session-Id" not in fake.requests[1]["headers"]


def test_notification_returns_202_empty_body() -> None:
    client = MCPHttpClient(config(), DummyLogger())
    client.session = FakeSession([response(status=202, text="", headers={})])

    assert client.initialized_notification() is None


def test_notification_http_error_raises() -> None:
    client = MCPHttpClient(config(), DummyLogger())
    client.session = FakeSession([response(status=500, payload={"error": "boom"})])

    with pytest.raises(MCPUnavailable):
        client.initialized_notification()


def test_sse_one_line_data() -> None:
    assert parse_sse_json('data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n')["id"] == 1


def test_sse_multi_line_data_event() -> None:
    parsed = parse_sse_json('data: {"jsonrpc":"2.0",\ndata: "id":1,\ndata: "result":{}}\n\n')

    assert parsed["id"] == 1


def test_sse_multiple_events_and_comments() -> None:
    text = ': comment\nevent: progress\ndata: not-json\n\n' 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{}}\n\n'

    assert parse_sse_json(text)["id"] == 2


def test_sse_no_valid_json_rpc_event_rejected() -> None:
    with pytest.raises(InvalidResponseSchema):
        parse_sse_json("data: not-json\n\n")


def test_structured_search_hits_and_trace_id_variants() -> None:
    raw = {
        "result": {
            "structuredContent": {
                "results": [
                    {"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}},
                    {"traceId": "b" * 32, "attributes": {"agent.run_id": "run-2"}},
                ]
            }
        }
    }

    hits = extract_trace_search_hits(raw)

    assert [hit.trace_id for hit in hits] == ["a" * 32, "b" * 32]
    assert select_trace_id_for_run_id(hits, "run-1") == "a" * 32


def test_search_hit_without_trace_id_and_natural_language_rejected() -> None:
    with pytest.raises(IncompleteMCPTelemetry):
        extract_trace_search_hits({"result": {"structuredContent": {"results": [{"name": "span"}]}}})
    with pytest.raises(IncompleteMCPTelemetry):
        extract_trace_search_hits({"result": {"content": [{"type": "text", "text": "trace abc"}]}})


def test_tool_discovery_expected_names_and_missing_details() -> None:
    details = {"name": "signoz_get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}}
    search = {"name": "signoz_search_traces", "inputSchema": {"properties": {"filter": {}}}}

    discovered = discover_mcp_trace_tools([details, search])

    assert discovered.details_name == "signoz_get_trace_details"
    assert discovered.search_name == "signoz_search_traces"
    with pytest.raises(MCPToolUnavailable):
        discover_mcp_trace_tools([search])


def test_tool_discovery_rejects_unrelated_or_unmappable_schema() -> None:
    unrelated = {"name": "trace_formatter", "inputSchema": {"properties": {"message": {}}}}
    bad_details = {
        "name": "signoz_get_trace_details",
        "inputSchema": {"properties": {"trace_id": {}, "tenant": {}}, "required": ["trace_id", "tenant"]},
    }

    with pytest.raises(MCPToolUnavailable):
        discover_mcp_trace_tools([unrelated])
    with pytest.raises(MCPToolUnavailable):
        discover_mcp_trace_tools([bad_details])


class FakeMCPClient:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def complete_trace_response(trace_id: str = "a" * 32) -> dict[str, Any]:
    root = complete_span()
    child = complete_span("root")
    root["trace_id"] = trace_id
    child["trace_id"] = trace_id
    return {"result": {"structuredContent": {"trace_id": trace_id, "spans": [root, child]}}}


def test_search_succeeds_details_retrieval_succeeds(tmp_path: Path) -> None:
    client = FakeMCPClient(
        [
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            complete_trace_response(),
            complete_trace_response(),
        ]
    )
    tools = MCPTraceTools(
        details={"name": "signoz_get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}},
        search={"name": "signoz_search_traces", "inputSchema": {"properties": {"filter": {}}}},
    )
    evidence = ProbeEvidence(source=Source.MCP, available=True)

    trace = try_mcp_attribute_search(client, config(), tools, evidence, tmp_path)  # type: ignore[arg-type]

    assert isinstance(trace, Trace)
    assert evidence.attribute_search.state == CapabilityState.OBSERVED
    assert client.calls[0][0] == "signoz_search_traces"
    assert client.calls[1][0] == "signoz_get_trace_details"


def test_search_succeeds_details_retrieval_fails(tmp_path: Path) -> None:
    client = FakeMCPClient(
        [
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            MCPUnavailable("details failed"),
        ]
    )
    tools = MCPTraceTools(
        details={"name": "signoz_get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}},
        search={"name": "signoz_search_traces", "inputSchema": {"properties": {"filter": {}}}},
    )
    evidence = ProbeEvidence(source=Source.MCP, available=True)

    with pytest.raises(MCPUnavailable):
        try_mcp_attribute_search(client, config(), tools, evidence, tmp_path)  # type: ignore[arg-type]


def test_search_to_details_rejects_trace_without_requested_run_id(tmp_path: Path) -> None:
    bad_trace = complete_trace_response()
    for span in bad_trace["result"]["structuredContent"]["spans"]:
        span["attributes"]["agent.run_id"] = "other"
    client = FakeMCPClient(
        [
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            bad_trace,
        ]
    )
    tools = MCPTraceTools(
        details={"name": "signoz_get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}},
        search={"name": "signoz_search_traces", "inputSchema": {"properties": {"filter": {}}}},
    )
    evidence = ProbeEvidence(source=Source.MCP, available=True)

    assert try_mcp_attribute_search(client, config(), tools, evidence, tmp_path) is None  # type: ignore[arg-type]
    assert evidence.attribute_search.state == CapabilityState.FAILED


def test_stability_same_logical_trace_passes_with_reordered_spans_and_timestamp_ignored() -> None:
    first = normalize_mcp_trace(complete_trace_response())
    second_raw = complete_trace_response()
    second_raw["result"]["structuredContent"]["spans"].reverse()
    second = normalize_mcp_trace(second_raw)

    assert first is not None and second is not None
    assert structural_signature(first) == structural_signature(second)


def test_stability_fails_for_different_trace_span_or_parent() -> None:
    first = normalize_mcp_trace(complete_trace_response("a" * 32))
    different_trace = normalize_mcp_trace(complete_trace_response("b" * 32))
    different_span_raw = complete_trace_response("a" * 32)
    different_span_raw["result"]["structuredContent"]["spans"][1]["span_id"] = "child2"
    different_span = normalize_mcp_trace(different_span_raw)
    different_parent_raw = complete_trace_response("a" * 32)
    different_parent_raw["result"]["structuredContent"]["spans"][1]["parent_span_id"] = "missing"
    different_parent = normalize_mcp_trace(different_parent_raw)

    assert first is not None and different_trace is not None
    assert different_span is not None and different_parent is not None
    assert structural_signature(first) != structural_signature(different_trace)
    assert structural_signature(first) != structural_signature(different_span)
    assert structural_signature(first) != structural_signature(different_parent)


def test_run_mcp_probe_preserves_health_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        def __init__(self, config: Gate2Config, logger: DummyLogger) -> None:
            pass

        def health(self) -> dict[str, Any]:
            raise MCPUnavailable("down")

    monkeypatch.setattr("mcp_probe.MCPHttpClient", Client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_health"
    assert evidence.blocker is not None


def test_run_mcp_probe_preserves_initialize_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        session_id = None

        def __init__(self, config: Gate2Config, logger: DummyLogger) -> None:
            pass

        def health(self) -> dict[str, Any]:
            return {"status_code": 200}

        def initialize(self) -> dict[str, Any]:
            raise InvalidResponseSchema("bad init")

    monkeypatch.setattr("mcp_probe.MCPHttpClient", Client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_initialize"
    assert "mcp_initialize" in evidence.errors[0]


def test_run_mcp_probe_preserves_tools_list_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        session_id = None

        def __init__(self, config: Gate2Config, logger: DummyLogger) -> None:
            pass

        def health(self) -> dict[str, Any]:
            return {"status_code": 200}

        def initialize(self) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

        def initialized_notification(self) -> None:
            return None

        def list_tools(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise InvalidResponseSchema("bad tools")

    monkeypatch.setattr("mcp_probe.MCPHttpClient", Client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_tools_list"


def test_run_mcp_probe_tool_discovery_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        session_id = None

        def __init__(self, config: Gate2Config, logger: DummyLogger) -> None:
            pass

        def health(self) -> dict[str, Any]:
            return {"status_code": 200}

        def initialize(self) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

        def initialized_notification(self) -> None:
            return None

        def list_tools(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            return [{"name": "unrelated", "inputSchema": {"properties": {}}}], {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": []},
            }

    monkeypatch.setattr("mcp_probe.MCPHttpClient", Client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_tool_discovery"
    assert evidence.blocker is not None
