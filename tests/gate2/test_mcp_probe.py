from __future__ import annotations

from mcp_probe import args_for_trace_details, args_for_trace_search, normalize_mcp_trace


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
