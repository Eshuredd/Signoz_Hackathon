from __future__ import annotations

from types import SimpleNamespace

import pytest

from gate3.evaluator import evaluate_run_bundle
from gate3b.main import main
from gate3b.runner import GitProvenance, RunnerDependencies, run_gate3b
from gate3b.scenarios import get_definition
from gate3b.trace_exporter import emit_traces
from conftest import make_trace


def clean_provenance() -> GitProvenance:
    return GitProvenance("1" * 40, True)


def test_cli_list_scenarios_without_api_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("SIGNOZ_API_KEY", raising=False)
    assert main(["--list-scenarios"]) == 0
    assert "pass_single_trace_correlated_logs" in capsys.readouterr().out


def test_cli_invalid_scenario_returns_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--scenario", "missing"])
    assert exc.value.code == 2


def test_runner_mismatch_allows_next_scenario_and_internal_stops(config) -> None:
    calls: list[str] = []
    definitions = (get_definition("pass_single_trace_without_logs"), get_definition("pass_single_trace_without_logs"))

    def client_factory(cfg):
        from exceptions import EmptySearchResults

        def search_traces(*args, **kwargs):
            raise EmptySearchResults("empty")

        def request_json(*args, **kwargs):
            return {"status": "success", "data": {"data": {"results": [{"rows": []}]}}}

        return SimpleNamespace(health_check=lambda: {"status": "ok"}, version=lambda: {"version": "v0.test"}, search_traces=search_traces, query_range=lambda payload: request_json())

    def trace_emit(scenario, cfg):
        calls.append(scenario.name)
        return emit_traces(scenario, cfg, otlp_exporter_factory=FakeTraceExporter)

    def trace_poll(client, scenario, emitted, **kwargs):
        trace = make_trace(emitted[0], scenario)
        return SimpleNamespace(traces=(trace,), discovered_trace_ids=emitted, stats=SimpleNamespace(search_attempt_count=1, retrieval_attempt_count=1, elapsed_ms=1))

    def log_emit(scenario, cfg, trace_emission):
        return SimpleNamespace(log_ids=(), expected_agent_run_ids={}, expected_trace_ids={}, expected_span_ids={}, bodies={}, to_dict=lambda: {"log_ids": []})

    def log_poll(client, scenario, expected, **kwargs):
        return SimpleNamespace(logs=(), stats=SimpleNamespace(search_attempt_count=0, retrieval_attempt_count=0, elapsed_ms=0))

    def bad_eval(bundle):
        result = evaluate_run_bundle(bundle)
        return result

    deps = RunnerDependencies(
        config_factory=lambda: config,
        client_factory=client_factory,
        trace_emit=trace_emit,
        log_emit=log_emit,
        trace_poll=trace_poll,
        log_poll=log_poll,
        evaluator=bad_eval,
        provenance_factory=clean_provenance,
        write_json=lambda path, payload: None,
    )
    code = run_gate3b(deps=deps)
    assert code in {0, 1}
    assert len(calls) == 4

    def boom_trace_emit(scenario, cfg):
        calls.append(scenario.name)
        raise RuntimeError("bug")

    calls.clear()
    deps = RunnerDependencies(config_factory=lambda: config, client_factory=client_factory, trace_emit=boom_trace_emit, provenance_factory=clean_provenance, write_json=lambda path, payload: None)
    assert run_gate3b(deps=deps) == 4
    assert calls == ["pass_single_trace_correlated_logs"]


class FakeTraceExporter:
    def __init__(self, **kwargs: object) -> None:
        pass

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int) -> bool:
        return True

    def shutdown(self) -> None:
        pass


def test_normal_runner_never_writes_committed_evidence(config) -> None:
    written: list[str] = []

    def writer(path, payload):
        written.append(str(path).replace("\\", "/"))

    def client_factory(cfg):
        return SimpleNamespace(
            health_check=lambda: {"status": "ok"},
            version=lambda: {"version": "v0.test"},
            search_traces=lambda *args, **kwargs: (_ for _ in ()).throw(__import__("exceptions").EmptySearchResults("empty")),
            query_range=lambda payload: {"status": "success", "data": {"data": {"results": [{"rows": []}]}}},
        )

    deps = RunnerDependencies(config_factory=lambda: config, client_factory=client_factory, provenance_factory=clean_provenance, write_json=writer)
    assert run_gate3b(check_environment_only=True, deps=deps) == 0
    assert any(path.startswith(".traceguard/runtime/gate3b/") for path in written)
    assert not any(path.startswith("gate3b/evidence/") for path in written)


def test_runner_rejects_dirty_source_before_export(config) -> None:
    calls: list[str] = []
    deps = RunnerDependencies(
        config_factory=lambda: config,
        provenance_factory=lambda: GitProvenance("1" * 40, False),
        trace_emit=lambda *args, **kwargs: calls.append("trace"),
        write_json=lambda path, payload: None,
    )
    assert run_gate3b(deps=deps) == 2
    assert calls == []
