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
    extract_search_result_items,
    extract_trace_search_hits,
    normalize_mcp_trace,
    observe_mcp_stability,
    parse_trace_search_hit,
    parse_sse_json,
    run_mcp_probe,
    select_trace_id_for_run_id,
    structural_signature,
    try_mcp_attribute_search,
    try_mcp_direct_lookup,
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


def config_with(
    *,
    trace_id: str | None = "a" * 32,
    run_id: str | None = "run-1",
) -> Gate2Config:
    item = config()
    return Gate2Config(
        signoz_base_url=item.signoz_base_url,
        signoz_trace_id=trace_id,
        agent_run_id=run_id,
        signoz_api_key=item.signoz_api_key,
        request_timeout_seconds=item.request_timeout_seconds,
        debug=item.debug,
        mcp_url=item.mcp_url,
        mcp_health_url=item.mcp_health_url,
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


def test_nested_signoz_query_rows_normalize_trace() -> None:
    root = complete_span()
    child = complete_span("root")
    root.pop("attributes")
    root.pop("resource_attributes")
    child.pop("attributes")
    child.pop("resource_attributes")
    root["service.name"] = "svc"
    child["service.name"] = "svc"
    raw = {
        "result": {
            "structuredContent": {
                "data": {
                    "data": {
                        "results": [
                            {
                                "queryName": "A",
                                "rows": [
                                    {"data": root, "timestamp": root["start_time"]},
                                    {"data": child, "timestamp": child["start_time"]},
                                ],
                            }
                        ]
                    }
                }
            }
        }
    }

    trace = normalize_mcp_trace(raw)

    assert trace is not None
    assert len(trace.spans) == 2
    assert trace.has_valid_parent_child_relationship()
    assert trace.spans[0].resource_attributes["service.name"] == "svc"


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


def test_search_arguments_use_required_query_field() -> None:
    search_tool = {
        "inputSchema": {
            "properties": {
                "filter": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["query"],
        }
    }

    assert args_for_trace_search(search_tool, "run-1") == {"query": "agent.run_id = 'run-1'"}


def test_search_arguments_reject_ambiguous_or_unsent_required_fields() -> None:
    ambiguous = {
        "inputSchema": {
            "properties": {
                "filter": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["filter", "query"],
        }
    }
    missing_required = {
        "inputSchema": {
            "properties": {"filter": {"type": "string"}},
            "required": ["query"],
        }
    }

    with pytest.raises(MCPToolUnavailable):
        args_for_trace_search(ambiguous, "run-1")
    with pytest.raises(MCPToolUnavailable):
        args_for_trace_search(missing_required, "run-1")


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


def test_search_rows_and_data_containers_are_accepted() -> None:
    rows_raw = {"result": {"structuredContent": {"rows": [{"data": {"traceID": "a" * 32}}]}}}
    data_raw = {"result": {"structuredContent": {"data": [{"trace_id": "b" * 32}]}}}
    nested_raw = {
        "result": {
            "structuredContent": {
                "data": {
                    "data": {
                        "results": [
                            {"rows": [{"data": {"trace_id": "c" * 32}}]},
                        ]
                    }
                }
            }
        }
    }

    assert extract_trace_search_hits(rows_raw)[0].trace_id == "a" * 32
    assert extract_trace_search_hits(data_raw)[0].trace_id == "b" * 32
    assert extract_trace_search_hits(nested_raw)[0].trace_id == "c" * 32


def test_select_trace_id_accepts_duplicate_rows_for_same_trace_without_attributes() -> None:
    hits = [
        parse_trace_search_hit({"data": {"trace_id": "a" * 32, "span_id": "root"}}),
        parse_trace_search_hit({"data": {"trace_id": "a" * 32, "span_id": "child"}}),
    ]

    assert all(hit is not None for hit in hits)
    assert select_trace_id_for_run_id([hit for hit in hits if hit is not None], "run-1") == "a" * 32


def test_json_content_results_container_is_accepted() -> None:
    raw = {
        "result": {
            "content": [
                {
                    "type": "json",
                    "json": {"results": [{"trace_id": "a" * 32}]},
                }
            ]
        }
    }

    assert extract_trace_search_hits(raw)[0].trace_id == "a" * 32


def test_unrelated_metadata_and_error_trace_id_rejected() -> None:
    metadata = {"result": {"structuredContent": {"metadata": {"trace_id": "a" * 32}}}}
    error = {"result": {"structuredContent": {"error": {"trace_id": "a" * 32}}}}

    with pytest.raises(IncompleteMCPTelemetry):
        extract_trace_search_hits(metadata)
    with pytest.raises(IncompleteMCPTelemetry):
        extract_trace_search_hits(error)


def test_invalid_length_trace_id_rejected() -> None:
    raw = {"result": {"structuredContent": {"results": [{"trace_id": "abc"}]}}}

    with pytest.raises(IncompleteMCPTelemetry):
        extract_trace_search_hits(raw)


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


def test_undocumented_tool_aliases_are_rejected() -> None:
    details_alias = {"name": "get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}}
    search_alias = {"name": "search_traces", "inputSchema": {"properties": {"filter": {}}}}

    with pytest.raises(MCPToolUnavailable):
        discover_mcp_trace_tools([details_alias, search_alias])


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


def test_search_tool_with_unsupported_required_field_rejected() -> None:
    details = {"name": "signoz_get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}}
    bad_search = {
        "name": "signoz_search_traces",
        "inputSchema": {"properties": {"filter": {}, "tenant": {}}, "required": ["filter", "tenant"]},
    }

    discovered = discover_mcp_trace_tools([details, bad_search])

    assert discovered.search is None


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


class ProbeClient:
    session_id: str | None = None

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def health(self) -> dict[str, Any]:
        return {"status_code": 200}

    def initialize(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": {}}

    def initialized_notification(self) -> None:
        return None

    def list_tools(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tools = [
            {"name": "signoz_get_trace_details", "inputSchema": {"properties": {"trace_id": {}}}},
            {"name": "signoz_search_traces", "inputSchema": {"properties": {"filter": {}}}},
        ]
        return tools, {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}

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


def test_stability_fails_for_changed_span_name_attribute_or_resource_keys() -> None:
    first = normalize_mcp_trace(complete_trace_response())
    changed_name_raw = complete_trace_response()
    changed_name_raw["result"]["structuredContent"]["spans"][1]["name"] = "renamed"
    changed_name = normalize_mcp_trace(changed_name_raw)
    changed_attr_raw = complete_trace_response()
    changed_attr_raw["result"]["structuredContent"]["spans"][1]["attributes"]["new.key"] = "value"
    changed_attr = normalize_mcp_trace(changed_attr_raw)
    changed_resource_raw = complete_trace_response()
    changed_resource_raw["result"]["structuredContent"]["spans"][1]["resource_attributes"]["new.resource"] = "value"
    changed_resource = normalize_mcp_trace(changed_resource_raw)

    assert first is not None and changed_name is not None
    assert changed_attr is not None and changed_resource is not None
    assert structural_signature(first) != structural_signature(changed_name)
    assert structural_signature(first) != structural_signature(changed_attr)
    assert structural_signature(first) != structural_signature(changed_resource)


def test_observe_mcp_stability_failure_keeps_stage(tmp_path: Path) -> None:
    first = normalize_mcp_trace(complete_trace_response())
    assert first is not None
    client = FakeMCPClient([complete_trace_response("b" * 32)])
    evidence = ProbeEvidence(source=Source.MCP, available=True)

    observe_mcp_stability(client, "signoz_get_trace_details", {"trace_id": "a" * 32}, first, evidence, tmp_path / "repeat.json")  # type: ignore[arg-type]

    assert evidence.response_stability.state == CapabilityState.FAILED
    assert evidence.failed_stage == "mcp_stability_check"


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


def test_run_mcp_probe_preserves_notification_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        session_id = None

        def __init__(self, config: Gate2Config, logger: DummyLogger) -> None:
            pass

        def health(self) -> dict[str, Any]:
            return {"status_code": 200}

        def initialize(self) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

        def initialized_notification(self) -> None:
            raise MCPUnavailable("notification failed")

    monkeypatch.setattr("mcp_probe.MCPHttpClient", Client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_initialized_notification"
    assert "mcp_initialized_notification" in evidence.errors[0]


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


def test_run_mcp_probe_preserves_search_parsing_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient([{"result": {"structuredContent": {"metadata": {"trace_id": "a" * 32}}}}])
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config_with(trace_id=None), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_search_result_parsing"
    assert "mcp_search_result_parsing" in evidence.errors[0]


def test_run_mcp_probe_preserves_details_from_search_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient(
        [
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            MCPUnavailable("details failed"),
        ]
    )
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config_with(trace_id=None), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_details_from_search"
    assert "mcp_details_from_search" in evidence.errors[0]


def test_run_mcp_probe_preserves_normalization_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient(
        [
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            {"result": {"structuredContent": {"summary": "no spans here"}}},
        ]
    )
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config_with(trace_id=None), DummyLogger(), tmp_path)

    assert evidence.failed_stage == "mcp_normalization"
    assert evidence.retrieval_workflow.state == CapabilityState.FAILED


def test_run_mcp_probe_preserves_stability_failure_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient([complete_trace_response("a" * 32), complete_trace_response("b" * 32)])
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config_with(run_id=None), DummyLogger(), tmp_path)

    assert evidence.response_stability.state == CapabilityState.FAILED
    assert evidence.failed_stage == "mcp_stability_check"
    assert any(error.startswith("mcp_stability_check:") for error in evidence.errors)


def test_run_mcp_probe_stability_failure_is_not_overwritten_by_search_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = ProbeClient(
        [
            complete_trace_response("a" * 32),
            complete_trace_response("b" * 32),
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            complete_trace_response("a" * 32),
            complete_trace_response("a" * 32),
        ]
    )
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.direct_lookup.state == CapabilityState.OBSERVED
    assert evidence.attribute_search.state == CapabilityState.OBSERVED
    assert evidence.response_stability.state == CapabilityState.FAILED
    assert evidence.failed_stage == "mcp_stability_check"


def test_run_mcp_probe_preserves_relationship_validation_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken = complete_trace_response("a" * 32)
    broken["result"]["structuredContent"]["spans"][1]["parent_span_id"] = "missing"
    client = ProbeClient([broken, broken])
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config_with(run_id=None), DummyLogger(), tmp_path)

    assert evidence.preserves_parent_child.state == CapabilityState.NOT_OBSERVED
    assert evidence.failed_stage == "mcp_relationship_validation"


def test_run_mcp_probe_direct_fails_while_search_to_details_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient(
        [
            MCPUnavailable("direct failed"),
            {"result": {"structuredContent": {"results": [{"trace_id": "a" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            complete_trace_response("a" * 32),
            complete_trace_response("a" * 32),
        ]
    )
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.direct_lookup.state == CapabilityState.FAILED
    assert evidence.attribute_search.state == CapabilityState.OBSERVED
    assert evidence.trace is not None
    assert evidence.retrieval_workflow.state == CapabilityState.OBSERVED


def test_run_mcp_probe_search_fails_while_direct_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient(
        [
            complete_trace_response("a" * 32),
            complete_trace_response("a" * 32),
            {"result": {"structuredContent": {"metadata": {"trace_id": "a" * 32}}}},
        ]
    )
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.direct_lookup.state == CapabilityState.OBSERVED
    assert evidence.attribute_search.state == CapabilityState.FAILED
    assert evidence.trace is not None
    assert evidence.retrieval_workflow.state == CapabilityState.OBSERVED


def test_run_mcp_probe_reports_direct_search_trace_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ProbeClient(
        [
            complete_trace_response("a" * 32),
            complete_trace_response("a" * 32),
            {"result": {"structuredContent": {"results": [{"trace_id": "b" * 32, "attributes": {"agent.run_id": "run-1"}}]}}},
            complete_trace_response("b" * 32),
            complete_trace_response("b" * 32),
        ]
    )
    monkeypatch.setattr("mcp_probe.MCPHttpClient", lambda config, logger: client)
    monkeypatch.setattr("mcp_probe.fetch_signoz_version", lambda config: "test")

    evidence = run_mcp_probe(config(), DummyLogger(), tmp_path)

    assert evidence.direct_lookup.state == CapabilityState.OBSERVED
    assert evidence.attribute_search.state == CapabilityState.OBSERVED
    assert evidence.retrieval_workflow.state == CapabilityState.FAILED
    assert evidence.failed_stage == "mcp_details_from_search"
    assert evidence.observations["mcp_direct_search_trace_id_match"] == "failed"
    assert any(error.startswith("mcp_details_from_search:") for error in evidence.errors)
