from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE2_ROOT = REPO_ROOT / "gate2"
if str(GATE2_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE2_ROOT))

from config import Gate2Config
from traceguard_runtime import Gate1RuntimeManifest, write_gate1_manifest


@pytest.fixture(autouse=True)
def clear_gate2_identifier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGNOZ_TRACE_ID", raising=False)
    monkeypatch.delenv("TRACEGUARD_AGENT_RUN_ID", raising=False)


def manifest(trace_id: str, run_id: str) -> Gate1RuntimeManifest:
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
        trace_export_succeeded=True,
        metric_export_succeeded=True,
        log_export_succeeded=True,
    )


def test_gate1_manifest_written_then_gate2_loads_same_pair(tmp_path: Path) -> None:
    manifest_path = write_gate1_manifest(
        manifest("a" * 32, "run-a"),
        tmp_path / "latest_gate1.json",
    )

    config = Gate2Config.from_env(
        dotenv_path=tmp_path / "missing.env",
        gate1_manifest_path=manifest_path,
    )

    assert config.signoz_trace_id == "a" * 32
    assert config.agent_run_id == "run-a"
    assert config.trace_context_source == "manifest"


def test_second_gate1_manifest_replaces_first_and_gate2_reads_fresh_pair(tmp_path: Path) -> None:
    manifest_path = write_gate1_manifest(
        manifest("a" * 32, "run-a"),
        tmp_path / "latest_gate1.json",
    )
    first = Gate2Config.from_env(
        dotenv_path=tmp_path / "missing.env",
        gate1_manifest_path=manifest_path,
    )

    write_gate1_manifest(manifest("b" * 32, "run-b"), manifest_path)
    second = Gate2Config.from_env(
        dotenv_path=tmp_path / "missing.env",
        gate1_manifest_path=manifest_path,
    )

    assert first.signoz_trace_id == "a" * 32
    assert first.agent_run_id == "run-a"
    assert second.signoz_trace_id == "b" * 32
    assert second.agent_run_id == "run-b"
