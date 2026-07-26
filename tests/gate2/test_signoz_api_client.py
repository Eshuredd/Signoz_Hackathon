from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from config import Gate2Config
from exceptions import EmptySearchResults, InvalidResponseSchema, TraceNotFound, UnsupportedAPIOperation
from models import CapabilityState
from signoz_api_client import (
    SigNozAPIClient,
    parse_json_response,
    parse_timestamp,
    parse_waterfall_trace,
    run_trace_api_probe,
)


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


def waterfall_response(
    *,
    parent_span_id: str = "",
    span_id: str = "root",
    attrs: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "spans": [
            {
                "trace_id": "a" * 32,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "name": "gate1",
                "time_unix": 1_767_225_600_000,
                "duration_nano": 1000,
                "status_code": 0,
                "attributes": attrs
                if attrs is not None
                else {
                    "agent.run_id": "run-1",
                    "traceguard.run_id": "run-1",
                    "traceguard.project": "TraceGuard",
                    "traceguard.gate": "1A",
                    "extra": "preserved",
                },
                "resource": {"service.name": "traceguard-gate1"},
            }
        ],
        "rootServiceName": "traceguard-gate1",
    }


def make_response(status: int, payload: dict[str, object], url: str = "http://x/api/v4/traces/a/waterfall") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = __import__("json").dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    return response


def test_timestamp_parsing_units_and_invalid() -> None:
    assert parse_timestamp("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_timestamp(1_767_225_600).year == 2026
    assert parse_timestamp(1_767_225_600_000).year == 2026
    assert parse_timestamp(1_767_225_600_000_000).year == 2026
    assert parse_timestamp(1_767_225_600_000_000_000).year == 2026
    assert parse_timestamp("not-a-time") is None


def test_parse_valid_waterfall_preserves_resource_and_unknown_attributes() -> None:
    trace = parse_waterfall_trace(waterfall_response(), source=__import__("models").Source.TRACE_API)

    assert len(trace.spans) == 1
    assert trace.spans[0].resource_attributes["service.name"] == "traceguard-gate1"
    assert trace.spans[0].attributes["extra"] == "preserved"


def test_parse_waterfall_missing_spans_schema() -> None:
    with pytest.raises(InvalidResponseSchema):
        parse_waterfall_trace({}, source=__import__("models").Source.TRACE_API)


def test_parse_waterfall_empty_spans_not_found() -> None:
    with pytest.raises(TraceNotFound):
        parse_waterfall_trace({"spans": []}, source=__import__("models").Source.TRACE_API)


def test_404_trace_specific_message_is_trace_not_found() -> None:
    with pytest.raises(TraceNotFound):
        parse_json_response(
            make_response(404, {"error": {"message": "trace not found"}}),
            "POST",
            "http://x/api/v4/traces/abc/waterfall",
        )


def test_404_generic_route_is_unsupported_api_operation() -> None:
    with pytest.raises(UnsupportedAPIOperation):
        parse_json_response(
            make_response(404, {"error": {"message": "page not found"}}),
            "POST",
            "http://x/api/v4/traces/abc/waterfall",
        )


def test_direct_lookup_success_survives_attribute_search_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def health(self: SigNozAPIClient) -> dict[str, object]:
        return {"status": "ok"}

    def version(self: SigNozAPIClient) -> dict[str, object]:
        return {"version": "test"}

    def auth_required(self: SigNozAPIClient) -> bool:
        return True

    def get_trace(self: SigNozAPIClient, trace_id: str):
        return parse_waterfall_trace(waterfall_response(), __import__("models").Source.TRACE_API), {"status": "success", "data": waterfall_response()}

    def search(self: SigNozAPIClient, run_id: str):
        raise EmptySearchResults("no rows")

    monkeypatch.setattr(SigNozAPIClient, "health_check", health)
    monkeypatch.setattr(SigNozAPIClient, "version", version)
    monkeypatch.setattr(SigNozAPIClient, "auth_required_check", auth_required)
    monkeypatch.setattr(SigNozAPIClient, "get_trace", get_trace)
    monkeypatch.setattr(SigNozAPIClient, "find_trace_by_run_id", search)

    evidence = run_trace_api_probe(config(), DummyLogger(), tmp_path)

    assert evidence.direct_lookup.state == CapabilityState.OBSERVED
    assert evidence.attribute_search.state == CapabilityState.NOT_OBSERVED
    assert evidence.trace is not None


def test_attribute_search_success_survives_direct_lookup_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {"get_trace": 0}

    def health(self: SigNozAPIClient) -> dict[str, object]:
        return {"status": "ok"}

    def version(self: SigNozAPIClient) -> dict[str, object]:
        return {"version": "test"}

    def auth_required(self: SigNozAPIClient) -> bool:
        return True

    def get_trace(self: SigNozAPIClient, trace_id: str):
        calls["get_trace"] += 1
        if calls["get_trace"] == 1:
            raise UnsupportedAPIOperation("route unavailable")
        return parse_waterfall_trace(waterfall_response(), __import__("models").Source.TRACE_API), {"status": "success", "data": waterfall_response()}

    def search(self: SigNozAPIClient, run_id: str):
        hit = __import__("models").TraceSearchHit(
            trace_id="a" * 32,
            span_id="root",
            span_name="gate1",
            attributes={},
            resource_attributes={},
            raw={},
        )
        return [hit], {"status": "success", "data": {"data": {"results": []}}}

    monkeypatch.setattr(SigNozAPIClient, "health_check", health)
    monkeypatch.setattr(SigNozAPIClient, "version", version)
    monkeypatch.setattr(SigNozAPIClient, "auth_required_check", auth_required)
    monkeypatch.setattr(SigNozAPIClient, "get_trace", get_trace)
    monkeypatch.setattr(SigNozAPIClient, "find_trace_by_run_id", search)

    evidence = run_trace_api_probe(config(), DummyLogger(), tmp_path)

    assert evidence.direct_lookup.state == CapabilityState.FAILED
    assert evidence.attribute_search.state == CapabilityState.OBSERVED
    assert evidence.trace is not None


def test_query_range_uses_public_authenticated_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SigNozAPIClient(config(), DummyLogger())
    calls = {}

    def request_json(method: str, path: str, *, json_body=None, auth: bool = True):
        calls.update({"method": method, "path": path, "json_body": json_body, "auth": auth})
        return {"status": "success", "data": {"data": {"results": []}}}

    monkeypatch.setattr(client, "_request_json", request_json)
    payload = {"x": 1}
    assert client.query_range(payload)["status"] == "success"
    assert calls == {"method": "POST", "path": "/api/v5/query_range", "json_body": payload, "auth": True}
