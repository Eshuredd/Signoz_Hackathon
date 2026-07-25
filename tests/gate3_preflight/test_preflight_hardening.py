from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from gate2.models import Source, Span, Trace
from gate3.models import RULESET_VERSION, EvaluationLevel
from gate3_preflight.config import PreflightConfig, PreflightConfigError, _append_traces_path
from gate3_preflight.exporter import EmissionResult, PreflightExportError, _verify_completed_spans, emit_scenario
from gate3_preflight.main import main, run_preflight
from gate3_preflight.scenarios import canonical_incomplete, canonical_valid, scenario_catalogue, scenarios
from gate3_preflight.trace_api_adapter import (
    AuthenticationFailure,
    AuthorizationFailure,
    ConfigurationError,
    ConnectionFailure,
    EmptySearchResults,
    InvalidResponseSchema,
    PreflightEnvironmentCheck,
    PreflightRetrievalError,
    RequestTimeout,
    TraceNotFound,
    UnsupportedAPIOperation,
    poll_and_retrieve,
)
from gate3_preflight.verification import verify_retrieved_trace


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:4318", "http://localhost:4318/v1/traces"),
        ("http://localhost:4318/", "http://localhost:4318/v1/traces"),
        ("http://localhost:4318/v1", "http://localhost:4318/v1/traces"),
        ("http://localhost:4318/v1/", "http://localhost:4318/v1/traces"),
        ("http://localhost:4318/v1/traces", "http://localhost:4318/v1/traces"),
        ("http://localhost:4318/v1/traces/", "http://localhost:4318/v1/traces"),
        ("https://collector.example.com/custom/base", "https://collector.example.com/custom/base/v1/traces"),
        ("https://collector.example.com/custom/base/", "https://collector.example.com/custom/base/v1/traces"),
    ],
)
def test_otlp_endpoint_normalisation(raw: str, expected: str) -> None:
    actual = _append_traces_path("OTLP", raw)
    assert actual == expected
    assert "/v1/v1/traces" not in actual
    assert "/v1/traces/v1/traces" not in actual


@pytest.mark.parametrize("raw", ["ftp://localhost:4318", "http:///v1/traces", "localhost:4318", "http://localhost:4318/v1?x=1", "http://localhost:4318/v1#frag"])
def test_otlp_endpoint_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(PreflightConfigError):
        _append_traces_path("OTLP", raw)


def test_list_scenarios_is_stable_without_config_or_network(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGNOZ_API_KEY", raising=False)
    assert main(["--list-scenarios"]) == 0
    first = capsys.readouterr().out
    assert main(["--list-scenarios"]) == 0
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload == scenario_catalogue()
    dumped = json.dumps(payload)
    assert "tg-preflight-" not in dumped
    assert "SIGNOZ_API_KEY" not in dumped


def test_invalid_cli_option_returns_argparse_exit_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--invalid-option"])
    assert exc.value.code == 2


class FakeExporter:
    instances: list["FakeExporter"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.shutdown_called = False
        FakeExporter.instances.append(self)

    def export(self, spans: object) -> SpanExportResult:
        self.spans = tuple(spans)  # type: ignore[arg-type]
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int) -> bool:
        self.timeout_millis = timeout_millis
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True


class ExportRaises(FakeExporter):
    def export(self, spans: object) -> SpanExportResult:
        super().export(spans)
        raise RuntimeError("network boom")


class FlushRaises(FakeExporter):
    def force_flush(self, timeout_millis: int) -> bool:
        raise RuntimeError("flush boom")


def test_exporter_constructor_timeout_and_exception_shutdown() -> None:
    FakeExporter.instances = []
    with pytest.raises(RuntimeError, match="network boom"):
        emit_scenario(canonical_valid(), config(), otlp_exporter_factory=ExportRaises)
    assert FakeExporter.instances[-1].shutdown_called is True

    with pytest.raises(RuntimeError, match="flush boom"):
        emit_scenario(canonical_valid(), config(), otlp_exporter_factory=FlushRaises)
    assert FakeExporter.instances[-1].shutdown_called is True

    result = emit_scenario(canonical_valid(), config(), otlp_exporter_factory=FakeExporter)
    assert result.exported is True
    assert FakeExporter.instances[-1].kwargs["timeout"] == config().otlp_timeout_seconds
    assert FakeExporter.instances[-1].kwargs["endpoint"] == config().otlp_endpoint


def fake_readable(name: str, trace_id: int = 1, span_id: int = 1, parent: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        context=SimpleNamespace(trace_id=trace_id, span_id=span_id),
        parent=None if parent is None else SimpleNamespace(span_id=parent),
    )


@pytest.mark.parametrize(
    "spans",
    [
        (fake_readable("agent.run", span_id=1), fake_readable("agent.run", span_id=2), fake_readable("model.call", span_id=3, parent=1)),
        (fake_readable("tool.call", span_id=1), fake_readable("agent.run", span_id=2), fake_readable("model.call", span_id=3, parent=2)),
        (fake_readable("agent.run", span_id=1), fake_readable("tool.call", span_id=2, parent=1)),
        (fake_readable("agent.run", span_id=1), fake_readable("tool.call", span_id=1, parent=1), fake_readable("model.call", span_id=3, parent=1)),
        (fake_readable("agent.run", trace_id=1, span_id=1), fake_readable("tool.call", trace_id=2, span_id=2, parent=1), fake_readable("model.call", trace_id=1, span_id=3, parent=1)),
    ],
)
def test_completed_span_verification_rejects_invalid_local_shapes(spans: tuple[SimpleNamespace, ...]) -> None:
    with pytest.raises(PreflightExportError):
        _verify_completed_spans(canonical_valid(), spans)  # type: ignore[arg-type]


def valid_trace(scenario=None, emission=None) -> tuple[object, EmissionResult, Trace]:
    scenario = scenario or canonical_valid()
    emission = emission or emit_scenario(scenario, config(), otlp_exporter_factory=FakeExporter)
    now = datetime.now(UTC)
    trace = Trace(
        emission.trace_id,
        [
            Span(emission.trace_id, emission.span_ids_by_name["agent.run"], None, "agent.run", now, now, 1, {}, dict(scenario.spans[0].attributes), {"service.name": "svc"}, "svc"),
            Span(emission.trace_id, emission.span_ids_by_name["tool.call"], emission.root_span_id, "tool.call", now, now, 1, {}, dict(scenario.spans[1].attributes), {"service.name": "svc"}, "svc"),
            Span(emission.trace_id, emission.span_ids_by_name["model.call"], emission.root_span_id, "model.call", now, now, 1, {}, dict(scenario.spans[2].attributes), {"service.name": "svc"}, "svc"),
        ],
        now,
        Source.TRACE_API,
    )
    return scenario, emission, trace


def with_spans(trace: Trace, spans: list[Span]) -> Trace:
    return Trace(trace.trace_id, spans, trace.retrieved_at, trace.source)


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda e, t: Trace("b" * 32, t.spans, t.retrieved_at, t.source), "trace_id_match"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], trace_id="b" * 32), *t.spans[1:]]), "trace_id_match"),
        (lambda e, t: with_spans(t, t.spans[:2]), "span_count_match"),
        (lambda e, t: with_spans(t, [*t.spans, replace(t.spans[0], span_id="4" * 16)]), "span_count_match"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], span_name="wrong"), *t.spans[1:]]), "span_names_match"),
        (lambda e, t: with_spans(t, [t.spans[0], replace(t.spans[1], span_name="agent.run"), t.spans[2]]), "span_names_match"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], span_id="9" * 16), *t.spans[1:]]), "span_ids_match"),
        (lambda e, t: with_spans(t, [t.spans[0], replace(t.spans[1], parent_span_id="bad"), t.spans[2]]), "parent_relationships_match"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], parent_span_id="bad"), *t.spans[1:]]), "parent_relationships_match"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], attributes={}), *t.spans[1:]]), "preflight_correlation_match"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], attributes={**t.spans[0].attributes, "agent.name": "changed"}), *t.spans[1:]]), "required_attributes_preserved"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], service_name=None, resource_attributes={}), *t.spans[1:]]), "service_identity_preserved"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], start_time=None), *t.spans[1:]]), "timing_preserved"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], end_time=None), *t.spans[1:]]), "timing_preserved"),
        (lambda e, t: with_spans(t, [replace(t.spans[0], duration_nano=None), *t.spans[1:]]), "timing_preserved"),
    ],
)
def test_preservation_verification_fields(mutate, field: str) -> None:
    scenario, emission, trace = valid_trace()
    result = verify_retrieved_trace(scenario=scenario, emission=emission, trace=mutate(emission, trace))
    assert getattr(result, field) is False
    assert result.errors


def test_preservation_valid_and_intentional_absence_cases() -> None:
    scenario, emission, trace = valid_trace()
    assert verify_retrieved_trace(scenario=scenario, emission=emission, trace=trace).passed is True

    scenario, emission, trace = valid_trace(canonical_incomplete())
    assert verify_retrieved_trace(scenario=scenario, emission=emission, trace=trace).passed is True
    injected = with_spans(trace, [replace(trace.spans[0], attributes={**trace.spans[0].attributes, "agent.name": "injected"}), *trace.spans[1:]])
    result = verify_retrieved_trace(scenario=scenario, emission=emission, trace=injected)
    assert result.intentional_absences_preserved is False


class Hit:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id


class PollClient:
    def __init__(self, searches: list[object], traces: list[object]) -> None:
        self.searches = searches
        self.traces = traces
        self.search_attempts = 0
        self.retrieval_attempts = 0

    def search_traces(self, *args, **kwargs):
        self.search_attempts += 1
        item = self.searches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {}

    def get_trace(self, trace_id: str):
        self.retrieval_attempts += 1
        item = self.traces.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {}


def clock(values: list[float]):
    last = values[-1]

    def _next() -> float:
        return values.pop(0) if values else last
    return _next


def test_poll_retries_empty_search_trace_not_found_incomplete_and_missing_correlation() -> None:
    _, _, trace = valid_trace()
    client = PollClient([EmptySearchResults("empty"), [Hit(trace.trace_id)]], [trace])
    sleeps: list[float] = []
    result = poll_and_retrieve(client, preflight_id=trace.spans[0].attributes["traceguard.preflight_id"], emitted_trace_id=trace.trace_id, timeout_seconds=10, interval_seconds=2, monotonic=clock([0, 0, 1, 1, 1]), sleeper=sleeps.append)
    assert result.search_attempt_count == 2
    assert sleeps == [2]

    client = PollClient([[Hit(trace.trace_id)], [Hit(trace.trace_id)]], [TraceNotFound("wait"), trace])
    sleeps = []
    result = poll_and_retrieve(client, preflight_id=trace.spans[0].attributes["traceguard.preflight_id"], emitted_trace_id=trace.trace_id, timeout_seconds=10, interval_seconds=2, monotonic=clock([0, 0, 1, 1, 1]), sleeper=sleeps.append)
    assert result.retrieval_attempt_count == 2
    assert sleeps == [2]

    short = Trace(trace.trace_id, trace.spans[:2], trace.retrieved_at, trace.source)
    client = PollClient([[Hit(trace.trace_id)], [Hit(trace.trace_id)]], [short, trace])
    result = poll_and_retrieve(client, preflight_id=trace.spans[0].attributes["traceguard.preflight_id"], emitted_trace_id=trace.trace_id, timeout_seconds=10, interval_seconds=2, monotonic=clock([0, 0, 1, 1, 1]), sleeper=lambda _: None)
    assert result.last_retry_reason == "retrieved_trace_incomplete_span_count"

    missing = with_spans(trace, [replace(trace.spans[0], attributes={}), *trace.spans[1:]])
    client = PollClient([[Hit(trace.trace_id)], [Hit(trace.trace_id)]], [missing, trace])
    result = poll_and_retrieve(client, preflight_id=trace.spans[0].attributes["traceguard.preflight_id"], emitted_trace_id=trace.trace_id, timeout_seconds=10, interval_seconds=2, monotonic=clock([0, 0, 1, 1, 1]), sleeper=lambda _: None)
    assert result.last_retry_reason == "retrieved_trace_missing_preflight_correlation"


@pytest.mark.parametrize("exc", [AuthenticationFailure("x"), AuthorizationFailure("x"), ConfigurationError("x"), InvalidResponseSchema("x"), UnsupportedAPIOperation("x"), ConnectionFailure("x"), RequestTimeout("x")])
def test_poll_does_not_retry_non_retry_failures(exc: Exception) -> None:
    client = PollClient([exc], [])
    with pytest.raises(exc.__class__):
        poll_and_retrieve(client, preflight_id="p", emitted_trace_id="a" * 32, timeout_seconds=10, interval_seconds=2, monotonic=lambda: 0, sleeper=lambda _: pytest.fail("slept"))
    assert client.search_attempts == 1


@pytest.mark.parametrize("searches,traces,error", [([[Hit("a" * 32), Hit("b" * 32)]], [], "exactly one"), ([[Hit("b" * 32)]], [], "Discovered"), ([[Hit("a" * 32)]], [Trace("b" * 32, [], datetime.now(UTC), Source.TRACE_API)], "Retrieved")])
def test_poll_does_not_retry_mismatch_failures(searches: list[object], traces: list[object], error: str) -> None:
    client = PollClient(searches, traces)
    with pytest.raises(PreflightRetrievalError, match=error):
        poll_and_retrieve(client, preflight_id="p", emitted_trace_id="a" * 32, timeout_seconds=10, interval_seconds=2, monotonic=lambda: 0, sleeper=lambda _: pytest.fail("slept"))


def test_poll_timeout_boundaries_are_deterministic() -> None:
    client = PollClient([EmptySearchResults("empty"), EmptySearchResults("still empty")], [])
    sleeps: list[float] = []
    with pytest.raises(PreflightRetrievalError, match="elapsed_ms=1000"):
        poll_and_retrieve(client, preflight_id="p", emitted_trace_id="a" * 32, timeout_seconds=1, interval_seconds=5, monotonic=clock([0, 0, 0, 1]), sleeper=sleeps.append)
    assert sleeps == [1]

    client = PollClient([EmptySearchResults("empty")], [])
    with pytest.raises(PreflightRetrievalError):
        poll_and_retrieve(client, preflight_id="p", emitted_trace_id="a" * 32, timeout_seconds=0, interval_seconds=5, monotonic=clock([2, 3, 3]), sleeper=lambda _: pytest.fail("slept"))
    assert client.search_attempts == 0


class EnvClient:
    def __init__(self, *, search_exc: Exception | None = None, health: dict[str, object] | None = None, version: dict[str, object] | None = None) -> None:
        self.search_exc = search_exc
        self.health = health or {"status": "ok", "details": {"secret": "not copied"}}
        self.version_payload = version or {"version": "v0.test"}

    def health_check(self) -> dict[str, object]:
        return self.health

    def version(self) -> dict[str, object]:
        return self.version_payload

    def search_traces(self, *args, **kwargs):
        if self.search_exc:
            raise self.search_exc
        raise EmptySearchResults("accepted")


def test_environment_check_structured_success_and_secret_safety() -> None:
    from gate3_preflight.trace_api_adapter import PreflightTraceAPIAdapter

    result = PreflightTraceAPIAdapter(EnvClient()).run_environment_check().to_dict()
    assert result["health_ok"] is True
    assert result["version_ok"] is True
    assert result["signoz_version"] == "v0.test"
    assert result["authenticated_trace_api_access"] is True
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("exc", [AuthenticationFailure("bad key"), AuthorizationFailure("forbidden"), ConnectionFailure("down")])
def test_environment_check_propagates_infrastructure_failures(exc: Exception) -> None:
    from gate3_preflight.trace_api_adapter import PreflightTraceAPIAdapter

    with pytest.raises(exc.__class__):
        PreflightTraceAPIAdapter(EnvClient(search_exc=exc)).run_environment_check()


def test_environment_check_rejects_bad_health_and_unexpected_errors() -> None:
    from gate3_preflight.trace_api_adapter import PreflightTraceAPIAdapter

    with pytest.raises(InvalidResponseSchema):
        PreflightTraceAPIAdapter(EnvClient(health={"status": "maybe"})).run_environment_check()

    class Broken(EnvClient):
        def health_check(self):
            raise AttributeError("bug")

    with pytest.raises(AttributeError):
        PreflightTraceAPIAdapter(Broken()).run_environment_check()


def test_check_environment_runner_does_not_export() -> None:
    calls = {"emit": 0}

    def emit(*args, **kwargs):
        calls["emit"] += 1

    code = run_preflight(
        check_environment_only=True,
        config_factory=config,
        client_factory=lambda cfg: EnvClient(),
        emit=emit,
        write_json=lambda path, payload: None,
    )
    assert code == 0
    assert calls["emit"] == 0


class EvalItem:
    def __init__(self, rule_id: str, status: str) -> None:
        self.rule_id = rule_id
        self.status = SimpleNamespace(value=status)


class Eval:
    def __init__(self, statuses: dict[str, str], verdict: str) -> None:
        self.rule_results = [EvalItem(k, v) for k, v in statuses.items()]
        self.verdict = SimpleNamespace(value=verdict)
        self.ruleset_version = RULESET_VERSION
        self.evaluation_level = EvaluationLevel.TRACE

    def to_dict(self) -> dict[str, object]:
        return {"verdict": self.verdict.value, "rule_results": {item.rule_id: item.status.value for item in self.rule_results}}


def runner_success_doubles(mismatch: bool = False):
    scenario_items = scenarios()
    emissions = []
    traces = []
    for scenario in scenario_items:
        scenario, emission, trace = valid_trace(scenario)
        emissions.append(emission)
        traces.append(trace)

    def emit(scenario, cfg):
        return emissions.pop(0)

    def poll(client, **kwargs):
        trace = traces.pop(0)
        return SimpleNamespace(trace=trace, discovered_trace_ids=(trace.trace_id,), search_attempt_count=1, retrieval_attempt_count=1, elapsed_ms=1)

    def evaluate(_trace):
        current = scenario_items[1 if len(emissions) == 0 else 0]
        statuses = dict(current.expected_statuses)
        verdict = current.expected_verdict
        if mismatch and current.name == "canonical_valid":
            statuses["TG-TEL-001"] = "FAILED"
        return Eval(statuses, verdict)

    return scenario_items, emit, poll, evaluate


def test_runner_exit_codes_success_contract_mismatch_config_infra_and_internal(capsys: pytest.CaptureFixture[str]) -> None:
    scenario_items, emit, poll, evaluate = runner_success_doubles()
    code = run_preflight(config_factory=config, client_factory=lambda cfg: EnvClient(), scenario_factory=lambda: scenario_items, emit=emit, poll=poll, evaluate=evaluate, write_json=lambda path, payload: None)
    assert code == 0

    scenario_items, emit, poll, evaluate = runner_success_doubles(mismatch=True)
    code = run_preflight(config_factory=config, client_factory=lambda cfg: EnvClient(), scenario_factory=lambda: scenario_items, emit=emit, poll=poll, evaluate=evaluate, write_json=lambda path, payload: None)
    assert code == 1
    assert "matched_expectations" in capsys.readouterr().out

    code = run_preflight(config_factory=lambda: (_ for _ in ()).throw(PreflightConfigError("missing key")), client_factory=lambda cfg: pytest.fail("env"), write_json=lambda path, payload: None)
    assert code == 2

    code = run_preflight(config_factory=config, client_factory=lambda cfg: EnvClient(search_exc=AuthenticationFailure("bad key")), write_json=lambda path, payload: None)
    assert code == 3

    code = run_preflight(config_factory=config, client_factory=lambda cfg: (_ for _ in ()).throw(AttributeError("bug")), write_json=lambda path, payload: None)
    assert code == 4

    scenario_items, emit, poll, evaluate = runner_success_doubles()
    code = run_preflight(config_factory=config, client_factory=lambda cfg: EnvClient(), scenario_factory=lambda: scenario_items, emit=emit, poll=poll, evaluate=evaluate, write_json=lambda path, payload: (_ for _ in ()).throw(OSError("disk full")))
    assert code == 4


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
