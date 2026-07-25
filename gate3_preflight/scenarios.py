from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from gate3.models import RuleStatus, Verdict, verdict_from_rule_results
from gate3.rules import RULE_BY_ID


@dataclass(frozen=True)
class SpanSpec:
    name: str
    attributes: dict[str, object]
    parent: str | None
    intentionally_absent_attributes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreflightScenario:
    name: str
    preflight_id: str
    agent_run_id: str
    spans: tuple[SpanSpec, ...]
    expected_verdict: str
    expected_statuses: dict[str, str]


CANONICAL_VALID_STATUSES = {
    "TG-TEL-001": "PASSED",
    "TG-TEL-002": "PASSED",
    "TG-TEL-003A": "PASSED",
    "TG-TEL-003B": "NOT_APPLICABLE",
    "TG-TEL-004": "PASSED",
    "TG-TEL-005": "PASSED",
    "TG-TEL-006": "PASSED",
    "TG-TEL-007": "PASSED",
    "TG-TEL-008": "NOT_APPLICABLE",
    "TG-STR-001": "PASSED",
    "TG-STR-002": "PASSED",
    "TG-STR-003": "PASSED",
    "TG-STR-004": "PASSED",
    "TG-STR-005": "PASSED",
}

CANONICAL_INCOMPLETE_STATUSES = {
    "TG-TEL-001": "PASSED",
    "TG-TEL-002": "FAILED",
    "TG-TEL-003A": "PASSED",
    "TG-TEL-003B": "NOT_APPLICABLE",
    "TG-TEL-004": "FAILED",
    "TG-TEL-005": "FAILED",
    "TG-TEL-006": "FAILED",
    "TG-TEL-007": "PASSED",
    "TG-TEL-008": "NOT_APPLICABLE",
    "TG-STR-001": "PASSED",
    "TG-STR-002": "PASSED",
    "TG-STR-003": "PASSED",
    "TG-STR-004": "PASSED",
    "TG-STR-005": "PASSED",
}


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
        expected_statuses=dict(CANONICAL_VALID_STATUSES),
    )


def canonical_incomplete() -> PreflightScenario:
    preflight_id = f"tg-preflight-{uuid4()}"
    run_id = f"run-{preflight_id}"
    return PreflightScenario(
        name="canonical_incomplete",
        preflight_id=preflight_id,
        agent_run_id=run_id,
        spans=(
            SpanSpec("agent.run", {"agent.run_id": run_id, "traceguard.preflight_id": preflight_id}, None, ("agent.name", "agent.status")),
            SpanSpec("tool.call", {"traceguard.preflight_id": preflight_id}, "agent.run", ("tool.status",)),
            SpanSpec(
                "model.call",
                {"traceguard.preflight_id": preflight_id},
                "agent.run",
                ("gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"),
            ),
        ),
        expected_verdict="BLOCK",
        expected_statuses=dict(CANONICAL_INCOMPLETE_STATUSES),
    )


def scenarios() -> tuple[PreflightScenario, PreflightScenario]:
    items = (canonical_valid(), canonical_incomplete())
    for item in items:
        validate_scenario_expectations(item)
    return items


def validate_scenario_expectations(scenario: PreflightScenario) -> None:
    registered = set(RULE_BY_ID)
    actual = set(scenario.expected_statuses)
    missing = sorted(registered - actual)
    unknown = sorted(actual - registered)
    if missing or unknown:
        raise ValueError(f"{scenario.name} expected statuses must exactly match registered rules; missing={missing}, unknown={unknown}")
    invalid = sorted(rule_id for rule_id, status in scenario.expected_statuses.items() if status not in {item.value for item in RuleStatus})
    if invalid:
        raise ValueError(f"{scenario.name} has invalid expected status for: {', '.join(invalid)}")
    if scenario.expected_verdict not in {item.value for item in Verdict}:
        raise ValueError(f"{scenario.name} has invalid expected verdict.")
    implied = _verdict_from_status_map(scenario.expected_statuses)
    if implied != scenario.expected_verdict:
        raise ValueError(f"{scenario.name} declares {scenario.expected_verdict} but statuses imply {implied}.")


def _verdict_from_status_map(statuses: dict[str, str]) -> str:
    synthetic = [
        RULE_BY_ID[rule_id].result(RuleStatus(status), "", observed={})
        for rule_id, status in statuses.items()
    ]
    return verdict_from_rule_results(synthetic).value
