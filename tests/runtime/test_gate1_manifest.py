from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from traceguard_runtime import (
    Gate1RuntimeManifest,
    RuntimeManifestError,
    default_gate1_manifest_path,
    read_gate1_manifest,
    repository_root,
    try_read_gate1_manifest,
    write_gate1_manifest,
)


def manifest(
    trace_id: str = "a" * 32,
    run_id: str = "run-1",
    *,
    trace_export_succeeded: bool = True,
    metric_export_succeeded: bool = True,
    log_export_succeeded: bool = True,
) -> Gate1RuntimeManifest:
    return Gate1RuntimeManifest(
        schema_version=1,
        generated_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        gate="1A",
        service_name="traceguard-gate1",
        service_version="0.1.0",
        span_name="traceguard.gate1.connectivity",
        trace_id=trace_id,
        agent_run_id=run_id,
        traceguard_run_id=run_id,
        trace_export_succeeded=trace_export_succeeded,
        metric_export_succeeded=metric_export_succeeded,
        log_export_succeeded=log_export_succeeded,
    )


def test_valid_manifest_serializes_deterministically() -> None:
    payload = manifest().to_dict()

    assert payload["schema_version"] == 1
    assert payload["generated_at"] == "2026-07-23T10:30:00Z"
    assert payload["trace_id"] == "a" * 32
    assert sorted(payload) == [
        "agent_run_id",
        "gate",
        "generated_at",
        "log_export_succeeded",
        "metric_export_succeeded",
        "schema_version",
        "service_name",
        "service_version",
        "span_name",
        "trace_export_succeeded",
        "trace_id",
        "traceguard_run_id",
    ]


def test_valid_manifest_round_trips_through_json(tmp_path: Path) -> None:
    path = write_gate1_manifest(manifest("b" * 32, "run-2"), tmp_path / "latest_gate1.json")

    loaded = read_gate1_manifest(path)

    assert loaded.trace_id == "b" * 32
    assert loaded.agent_run_id == "run-2"
    assert loaded.generated_at == datetime(2026, 7, 23, 10, 30, tzinfo=UTC)


def test_default_path_resolves_from_repository_not_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    assert repository_root().name == "Signoz_Hackathon"
    assert default_gate1_manifest_path() == repository_root() / ".traceguard" / "runtime" / "latest_gate1.json"


def test_parent_directories_are_created_and_output_has_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "runtime" / "latest_gate1.json"

    write_gate1_manifest(manifest(), path)

    assert path.exists()
    assert path.read_text().endswith("\n")


def test_atomic_write_replaces_older_manifest(tmp_path: Path) -> None:
    path = write_gate1_manifest(manifest("a" * 32, "run-1"), tmp_path / "latest_gate1.json")

    write_gate1_manifest(manifest("b" * 32, "run-2"), path)

    loaded = read_gate1_manifest(path)
    assert loaded.trace_id == "b" * 32
    assert loaded.agent_run_id == "run-2"


def test_missing_file_try_read_returns_none_and_read_fails(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    assert try_read_gate1_manifest(path) is None
    with pytest.raises(RuntimeManifestError, match="missing"):
        read_gate1_manifest(path)


def test_invalid_json_and_non_object_json_fail_clearly(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json")
    non_object = tmp_path / "list.json"
    non_object.write_text("[]")

    with pytest.raises(RuntimeManifestError, match="invalid JSON"):
        read_gate1_manifest(invalid)
    with pytest.raises(RuntimeManifestError, match="JSON object"):
        read_gate1_manifest(non_object)


def test_unsupported_schema_version_fails_clearly(tmp_path: Path) -> None:
    payload = manifest().to_dict()
    payload["schema_version"] = 2
    path = tmp_path / "latest_gate1.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(RuntimeManifestError, match="schema_version"):
        read_gate1_manifest(path)


def test_invalid_trace_id_and_missing_run_id_fail_clearly(tmp_path: Path) -> None:
    bad_trace = manifest().to_dict()
    bad_trace["trace_id"] = "bad"
    no_run = manifest().to_dict()
    no_run["agent_run_id"] = ""
    bad_trace_path = tmp_path / "bad_trace.json"
    no_run_path = tmp_path / "no_run.json"
    bad_trace_path.write_text(json.dumps(bad_trace))
    no_run_path.write_text(json.dumps(no_run))

    with pytest.raises(RuntimeManifestError, match="trace_id"):
        read_gate1_manifest(bad_trace_path)
    with pytest.raises(RuntimeManifestError, match="agent_run_id"):
        read_gate1_manifest(no_run_path)


def test_mismatched_run_ids_fail_clearly(tmp_path: Path) -> None:
    payload = manifest().to_dict()
    payload["traceguard_run_id"] = "other"
    path = tmp_path / "latest_gate1.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(RuntimeManifestError, match="must match"):
        read_gate1_manifest(path)


def test_required_export_failures_make_manifest_unusable() -> None:
    with pytest.raises(RuntimeManifestError, match="trace_export_succeeded"):
        manifest(trace_export_succeeded=False)
    with pytest.raises(RuntimeManifestError, match="metric_export_succeeded"):
        manifest(metric_export_succeeded=False)


def test_optional_log_failure_remains_valid(tmp_path: Path) -> None:
    path = write_gate1_manifest(
        manifest(log_export_succeeded=False),
        tmp_path / "latest_gate1.json",
    )

    assert read_gate1_manifest(path).log_export_succeeded is False


def test_secret_like_fields_are_not_serialized() -> None:
    encoded = json.dumps(manifest().to_dict())

    assert "SIGNOZ_API_KEY" not in encoded
    assert "Authorization" not in encoded
    assert "Bearer" not in encoded
    assert "Mcp-Session-Id" not in encoded
