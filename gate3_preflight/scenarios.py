from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class SpanSpec:
    name: str
    attributes: dict[str, object]
    parent: str | None


@dataclass(frozen=True)
class PreflightScenario:
    name: str
    preflight_id: str
    agent_run_id: str
    spans: tuple[SpanSpec, ...]
    expected_verdict: str
    expected_statuses: dict[str, str]


def canonical_valid() -> PreflightScenario:
    preflight_id = f"tg-preflight-{uuid4()}"
    run_id = f"run-{preflight_id}"
    return PreflightScenario(
        name="canonical_valid",
        preflight_id=preflight_id,
        agent_run_id=run_id,
        spans=(
            SpanSpec("agent.run", {"agent.run_id": run_id, "agent.name": "traceguard-preflight", "agent.status": "success", "traceguard.preflight_id": preflight_id}, None),
            SpanSpec("tool.call", {"tool.status": "success", "traceguard.preflight_id": preflight_id}, "agent.run"),
            SpanSpec("model.call", {"gen_ai.request.model": "gpt-preflight", "gen_ai.usage.input_tokens": 3, "gen_ai.usage.output_tokens": 5, "traceguard.preflight_id": preflight_id}, "agent.run"),
        ),
        expected_verdict="PASS",
        expected_statuses={"TG-TEL-003B": "NOT_APPLICABLE", "TG-TEL-008": "NOT_APPLICABLE"},
    )


def canonical_incomplete() -> PreflightScenario:
    preflight_id = f"tg-preflight-{uuid4()}"
    run_id = f"run-{preflight_id}"
    return PreflightScenario(
        name="canonical_incomplete",
        preflight_id=preflight_id,
        agent_run_id=run_id,
        spans=(
            SpanSpec("agent.run", {"agent.run_id": run_id, "agent.name": "traceguard-preflight", "agent.status": "error", "traceguard.preflight_id": preflight_id}, None),
            SpanSpec("tool.call", {"traceguard.preflight_id": preflight_id}, "agent.run"),
            SpanSpec("model.call", {"gen_ai.request.model": "gpt-preflight", "traceguard.preflight_id": preflight_id}, "agent.run"),
        ),
        expected_verdict="BLOCK",
        expected_statuses={"TG-TEL-004": "FAILED", "TG-TEL-006": "FAILED", "TG-TEL-003B": "NOT_APPLICABLE", "TG-TEL-008": "NOT_APPLICABLE"},
    )


def scenarios() -> tuple[PreflightScenario, PreflightScenario]:
    return (canonical_valid(), canonical_incomplete())
