from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE1_ROOT = REPO_ROOT / "gate1"
if str(GATE1_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE1_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import telemetry
from traceguard_runtime import read_gate1_manifest, write_gate1_manifest


def isolate_otlp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TRACEGUARD_OTLP_TRACES_ENDPOINT",
        "TRACEGUARD_OTLP_METRICS_ENDPOINT",
        "TRACEGUARD_OTLP_LOGS_ENDPOINT",
        "TRACEGUARD_OTLP_ENDPOINT",
        "TRACEGUARD_OTLP_TIMEOUT_SECONDS",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def install_successful_exports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    trace_ids: list[str],
    log_results: list[bool] | None = None,
) -> tuple[Path, list[tuple[str, str]]]:
    manifest_path = tmp_path / "latest_gate1.json"
    calls: list[tuple[str, str]] = []
    remaining_trace_ids = list(trace_ids)
    remaining_log_results = list(log_results or [True] * len(trace_ids))

    def export_trace(otel_resource: object, endpoint: str, timeout_ms: int, run_id: str) -> str:
        calls.append(("trace", run_id))
        return remaining_trace_ids.pop(0)

    def export_metric(otel_resource: object, endpoint: str, timeout_ms: int, run_id: str) -> None:
        calls.append(("metric", run_id))

    def emit_log(otel_resource: object, endpoint: str, timeout_ms: int, run_id: str) -> bool:
        calls.append(("log", run_id))
        return remaining_log_results.pop(0)

    def write_manifest(manifest: object) -> Path:
        return write_gate1_manifest(manifest, manifest_path)  # type: ignore[arg-type]

    monkeypatch.setattr(telemetry, "export_trace", export_trace)
    monkeypatch.setattr(telemetry, "export_metric", export_metric)
    monkeypatch.setattr(telemetry, "emit_optional_structured_log", emit_log)
    monkeypatch.setattr(telemetry, "write_gate1_manifest", write_manifest)
    isolate_otlp_env(monkeypatch)
    return manifest_path, calls


def test_successful_trace_and_metric_export_writes_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _ = install_successful_exports(monkeypatch, tmp_path, trace_ids=["a" * 32])

    assert telemetry.main() == 0
    loaded = read_gate1_manifest(manifest_path)
    assert loaded.trace_id == "a" * 32
    assert loaded.trace_export_succeeded is True
    assert loaded.metric_export_succeeded is True


def test_manifest_contains_same_run_id_for_trace_metric_and_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, calls = install_successful_exports(monkeypatch, tmp_path, trace_ids=["b" * 32])

    assert telemetry.main() == 0

    loaded = read_gate1_manifest(manifest_path)
    run_ids = {run_id for _, run_id in calls}
    assert run_ids == {loaded.agent_run_id}
    assert loaded.traceguard_run_id == loaded.agent_run_id


def test_optional_log_success_and_failure_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _ = install_successful_exports(
        monkeypatch,
        tmp_path,
        trace_ids=["c" * 32, "d" * 32],
        log_results=[True, False],
    )

    assert telemetry.main() == 0
    assert read_gate1_manifest(manifest_path).log_export_succeeded is True
    assert telemetry.main() == 0
    assert read_gate1_manifest(manifest_path).log_export_succeeded is False


def test_trace_export_failure_does_not_write_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "latest_gate1.json"

    def export_trace(otel_resource: object, endpoint: str, timeout_ms: int, run_id: str) -> str:
        raise telemetry.TelemetryExportError("trace failed")

    monkeypatch.setattr(telemetry, "export_trace", export_trace)
    isolate_otlp_env(monkeypatch)

    assert telemetry.main() == 1
    assert not manifest_path.exists()


def test_metric_export_failure_does_not_write_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "latest_gate1.json"

    def export_trace(otel_resource: object, endpoint: str, timeout_ms: int, run_id: str) -> str:
        return "e" * 32

    def export_metric(otel_resource: object, endpoint: str, timeout_ms: int, run_id: str) -> None:
        raise telemetry.TelemetryExportError("metric failed")

    def write_manifest(manifest: object) -> Path:
        return write_gate1_manifest(manifest, manifest_path)  # type: ignore[arg-type]

    monkeypatch.setattr(telemetry, "export_trace", export_trace)
    monkeypatch.setattr(telemetry, "export_metric", export_metric)
    monkeypatch.setattr(telemetry, "write_gate1_manifest", write_manifest)
    isolate_otlp_env(monkeypatch)

    assert telemetry.main() == 1
    assert not manifest_path.exists()


def test_manifest_write_failure_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_successful_exports(monkeypatch, tmp_path, trace_ids=["f" * 32])

    def write_manifest(manifest: object) -> Path:
        raise OSError("cannot write manifest")

    monkeypatch.setattr(telemetry, "write_gate1_manifest", write_manifest)

    assert telemetry.main() == 1


def test_second_successful_run_replaces_first_manifest_and_uses_new_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _ = install_successful_exports(
        monkeypatch,
        tmp_path,
        trace_ids=["1" * 32, "2" * 32],
    )

    assert telemetry.main() == 0
    first = read_gate1_manifest(manifest_path)
    assert telemetry.main() == 0
    second = read_gate1_manifest(manifest_path)

    assert first.trace_id == "1" * 32
    assert second.trace_id == "2" * 32
    assert first.agent_run_id != second.agent_run_id
