from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gate2.models import Source, Span, Trace
from gate3.evaluator import evaluate_trace
from gate3.trace_loader import load_trace_payload
from gate3_preflight.bridge import gate2_trace_to_gate3_envelope
from gate3_preflight.scenarios import canonical_incomplete, canonical_valid
from gate3_preflight.trace_api_adapter import PreflightRetrievalError, poll_and_retrieve


def test_valid_and_incomplete_scenarios_are_three_span_structures() -> None:
    valid = canonical_valid()
    incomplete = canonical_incomplete()
    assert [span.name for span in valid.spans] == ["agent.run", "tool.call", "model.call"]
    assert [span.name for span in incomplete.spans] == ["agent.run", "tool.call", "model.call"]
    assert "tool.status" not in incomplete.spans[1].attributes
    assert "traceguard.preflight_id" in valid.spans[0].attributes


def test_bridge_preserves_missing_attributes_and_relationships() -> None:
    trace = Trace(
        trace_id="a" * 32,
        spans=[
            Span("a" * 32, "1111111111111111", None, "agent.run", datetime.now(UTC), datetime.now(UTC), 1, {}, {"agent.run_id": "run-1", "agent.name": "a", "agent.status": "ok"}, {"service.name": "svc"}, "svc"),
            Span("a" * 32, "2222222222222222", "1111111111111111", "tool.call", datetime.now(UTC), datetime.now(UTC), 1, {}, {}, {"service.name": "svc"}, "svc"),
        ],
        retrieved_at=datetime.now(UTC),
        source=Source.TRACE_API,
    )
    envelope = gate2_trace_to_gate3_envelope(trace)
    loaded = load_trace_payload(envelope)
    assert loaded.spans[1].attributes == {}
    assert loaded.spans[1].parent_span_id == "1111111111111111"
    assert evaluate_trace(loaded).verdict.value == "BLOCK"


class EmptySearch(Exception):
    pass


class FakeHit:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def search_traces(self, filters, **kwargs):
        self.calls += 1
        assert filters == {"traceguard.preflight_id": "preflight-1"}
        if self.calls == 1:
            from exceptions import EmptySearchResults

            raise EmptySearchResults("not indexed")
        return [FakeHit("a" * 32)], {}

    def get_trace(self, trace_id: str):
        return object_with_trace_id(trace_id), {}


def object_with_trace_id(trace_id: str):
    class Obj:
        pass

    obj = Obj()
    obj.trace_id = trace_id
    return obj


def test_polling_uses_monotonic_and_fake_sleep() -> None:
    ticks = [0.0, 1.0, 2.0]
    sleeps: list[float] = []
    result = poll_and_retrieve(FakeClient(), preflight_id="preflight-1", emitted_trace_id="a" * 32, timeout_seconds=10, interval_seconds=2, monotonic=lambda: ticks.pop(0), sleeper=sleeps.append)
    assert result.discovered_trace_ids == ("a" * 32,)
    assert sleeps == [2]


def test_polling_does_not_retry_schema_mismatch() -> None:
    class BadClient(FakeClient):
        def search_traces(self, filters, **kwargs):
            return [FakeHit("b" * 32)], {}

    with pytest.raises(PreflightRetrievalError, match="did not match emitted"):
        poll_and_retrieve(BadClient(), preflight_id="preflight-1", emitted_trace_id="a" * 32, timeout_seconds=10, interval_seconds=2, monotonic=lambda: 0, sleeper=lambda _: None)
