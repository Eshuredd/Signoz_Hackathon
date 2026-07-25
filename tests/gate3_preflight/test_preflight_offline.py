from __future__ import annotations

from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from gate2.models import Source, Span, Trace
from gate3.evaluator import evaluate_trace
from gate3.trace_loader import load_trace_payload
from gate3_preflight.bridge import gate2_trace_to_gate3_envelope
from gate3_preflight.config import PreflightConfig
from gate3_preflight.exporter import PreflightExportError, emit_scenario
from gate3_preflight.scenarios import canonical_incomplete, canonical_valid
from gate3_preflight.trace_api_adapter import PreflightRetrievalError, poll_and_retrieve
from gate3_preflight.verification import verify_retrieved_trace


def test_valid_and_incomplete_scenarios_are_three_span_structures() -> None:
    valid = canonical_valid()
    incomplete = canonical_incomplete()
    assert [span.name for span in valid.spans] == ["agent.run", "tool.call", "model.call"]
    assert [span.name for span in incomplete.spans] == ["agent.run", "tool.call", "model.call"]
    assert len(incomplete.spans) == 3
    assert incomplete.spans[0].parent is None
    assert incomplete.spans[1].parent == "agent.run"
    assert incomplete.spans[2].parent == "agent.run"
    assert "agent.run_id" in incomplete.spans[0].attributes
    assert "agent.name" not in incomplete.spans[0].attributes
    assert "agent.status" not in incomplete.spans[0].attributes
    assert "tool.status" not in incomplete.spans[1].attributes
    assert "gen_ai.request.model" not in incomplete.spans[2].attributes
    assert "gen_ai.usage.input_tokens" not in incomplete.spans[2].attributes
    assert "gen_ai.usage.output_tokens" not in incomplete.spans[2].attributes
    assert all("traceguard.preflight_id" in span.attributes for span in incomplete.spans)
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
    now = datetime.now(UTC)
    return Trace(
        trace_id=trace_id,
        spans=[
            Span(trace_id, "1" * 16, None, "agent.run", now, now, 1, {}, {"traceguard.preflight_id": "preflight-1"}, {"service.name": "svc"}, "svc"),
            Span(trace_id, "2" * 16, "1" * 16, "tool.call", now, now, 1, {}, {"traceguard.preflight_id": "preflight-1"}, {"service.name": "svc"}, "svc"),
            Span(trace_id, "3" * 16, "1" * 16, "model.call", now, now, 1, {}, {"traceguard.preflight_id": "preflight-1"}, {"service.name": "svc"}, "svc"),
        ],
        retrieved_at=now,
        source=Source.TRACE_API,
    )


def test_polling_uses_monotonic_and_fake_sleep() -> None:
    current = 0.0

    def tick() -> float:
        nonlocal current
        value = current
        current += 1.0
        return value

    sleeps: list[float] = []
    result = poll_and_retrieve(FakeClient(), preflight_id="preflight-1", emitted_trace_id="a" * 32, timeout_seconds=10, interval_seconds=2, monotonic=tick, sleeper=sleeps.append)
    assert result.discovered_trace_ids == ("a" * 32,)
    assert sleeps == [2]


def test_polling_does_not_retry_schema_mismatch() -> None:
    class BadClient(FakeClient):
        def search_traces(self, filters, **kwargs):
            return [FakeHit("b" * 32)], {}

    with pytest.raises(PreflightRetrievalError, match="did not match emitted"):
        poll_and_retrieve(BadClient(), preflight_id="preflight-1", emitted_trace_id="a" * 32, timeout_seconds=10, interval_seconds=2, monotonic=lambda: 0, sleeper=lambda _: None)


class FakeExporter:
    instances: list["FakeExporter"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.shutdown_called = False
        self.exported_count = 0
        FakeExporter.instances.append(self)

    def export(self, spans: object) -> SpanExportResult:
        self.exported_count = len(spans)  # type: ignore[arg-type]
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True


class FailingExporter(FakeExporter):
    def export(self, spans: object) -> SpanExportResult:
        super().export(spans)
        return SpanExportResult.FAILURE


class FlushFailExporter(FakeExporter):
    def force_flush(self, timeout_millis: int) -> bool:
        return False


def test_exporter_verifies_local_spans_and_otlp_success() -> None:
    FakeExporter.instances = []
    result = emit_scenario(canonical_valid(), config(), otlp_exporter_factory=FakeExporter)
    assert result.completed_span_count == 3
    assert result.exported is True
    assert result.parent_span_ids_by_name["agent.run"] is None
    assert result.parent_span_ids_by_name["tool.call"] == result.root_span_id
    assert result.parent_span_ids_by_name["model.call"] == result.root_span_id
    assert len(set(result.span_ids_by_name.values())) == 3
    assert FakeExporter.instances[-1].exported_count == 3
    assert FakeExporter.instances[-1].shutdown_called is True


def test_exporter_rejects_otlp_failure_and_flush_failure() -> None:
    FakeExporter.instances = []
    with pytest.raises(PreflightExportError, match="SpanExportResult.SUCCESS"):
        emit_scenario(canonical_valid(), config(), otlp_exporter_factory=FailingExporter)
    assert FakeExporter.instances[-1].shutdown_called is True
    with pytest.raises(PreflightExportError, match="force_flush"):
        emit_scenario(canonical_valid(), config(), otlp_exporter_factory=FlushFailExporter)


def test_verification_detects_preservation_failures() -> None:
    scenario = canonical_valid()
    emission = emit_scenario(scenario, config(), otlp_exporter_factory=FakeExporter)
    now = datetime.now(UTC)
    trace = Trace(
        emission.trace_id,
        [
            Span(emission.trace_id, emission.span_ids_by_name["agent.run"], None, "agent.run", now, now, 1, {}, scenario.spans[0].attributes, {"service.name": "svc"}, "svc"),
            Span(emission.trace_id, emission.span_ids_by_name["tool.call"], emission.root_span_id, "tool.call", now, now, 1, {}, scenario.spans[1].attributes, {"service.name": "svc"}, "svc"),
            Span(emission.trace_id, emission.span_ids_by_name["model.call"], emission.root_span_id, "model.call", now, now, 1, {}, scenario.spans[2].attributes, {"service.name": "svc"}, "svc"),
        ],
        now,
        Source.TRACE_API,
    )
    assert verify_retrieved_trace(scenario=scenario, emission=emission, trace=trace).passed is True
    broken = Trace(
        trace.trace_id,
        [trace.spans[0], Span(emission.trace_id, emission.span_ids_by_name["tool.call"], "bad", "tool.call", now, now, 1, {}, scenario.spans[1].attributes, {"service.name": "svc"}, "svc"), trace.spans[2]],
        now,
        Source.TRACE_API,
    )
    assert verify_retrieved_trace(scenario=scenario, emission=emission, trace=broken).parent_relationships_match is False


def config() -> PreflightConfig:
    return PreflightConfig(
        signoz_base_url="http://localhost:8080",
        signoz_api_key="secret",
        otlp_endpoint="http://localhost:4318/v1/traces",
        request_timeout_seconds=1,
        poll_timeout_seconds=5,
        poll_interval_seconds=1,
        otlp_timeout_seconds=1,
        service_name="svc",
    )


def test_config_requires_api_key_and_valid_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    from gate3_preflight.config import PreflightConfigError

    monkeypatch.setenv("SIGNOZ_API_KEY", " ")
    with pytest.raises(PreflightConfigError, match="SIGNOZ_API_KEY"):
        PreflightConfig.from_env()

    monkeypatch.setenv("SIGNOZ_API_KEY", "secret-value")
    monkeypatch.setenv("TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS", "2")
    with pytest.raises(PreflightConfigError, match="POLL_INTERVAL"):
        PreflightConfig.from_env()


def test_config_otlp_endpoint_precedence_and_secret_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_API_KEY", "secret-value")
    monkeypatch.setenv("TRACEGUARD_OTLP_ENDPOINT", "http://ignored:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://chosen:4318/v1/traces")
    monkeypatch.delenv("TRACEGUARD_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("TRACEGUARD_PREFLIGHT_POLL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TRACEGUARD_PREFLIGHT_POLL_INTERVAL_SECONDS", raising=False)
    loaded = PreflightConfig.from_env()
    assert loaded.otlp_endpoint == "http://chosen:4318/v1/traces"
    snapshot = loaded.non_secret_snapshot()
    assert snapshot["SIGNOZ_API_KEY"] == "<set>"
    assert "secret-value" not in json_dumps(snapshot)


def json_dumps(payload: object) -> str:
    import json

    return json.dumps(payload)
