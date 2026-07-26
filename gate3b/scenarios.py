from __future__ import annotations

import json
from uuid import uuid4

from gate3.models import RuleStatus, Verdict, verdict_from_rule_results
from gate3.rules import RULE_BY_ID

from .models import RULESET_VERSION, LogSpec, RuntimeScenario, ScenarioDefinition, TraceSpec


ALL_PASSED = {
    "TG-TEL-001": "PASSED",
    "TG-TEL-002": "PASSED",
    "TG-TEL-003A": "PASSED",
    "TG-TEL-003B": "PASSED",
    "TG-TEL-004": "PASSED",
    "TG-TEL-005": "PASSED",
    "TG-TEL-006": "PASSED",
    "TG-TEL-007": "PASSED",
    "TG-TEL-008": "PASSED",
    "TG-STR-001": "PASSED",
    "TG-STR-002": "PASSED",
    "TG-STR-003": "PASSED",
    "TG-STR-004": "PASSED",
    "TG-STR-005": "PASSED",
}


def _with(**updates: str) -> dict[str, str]:
    statuses = dict(ALL_PASSED)
    statuses.update(updates)
    return statuses


SCENARIO_DEFINITIONS = (
    ScenarioDefinition(
        "pass_single_trace_correlated_logs",
        "One canonical valid trace with two retrieved logs correlated by agent.run_id, trace_id, and span_id.",
        1,
        2,
        "PASS",
        _with(),
        (TraceSpec("primary"),),
        (LogSpec("root-log", "agent.run"), LogSpec("child-log", "tool.call")),
    ),
    ScenarioDefinition(
        "pass_single_trace_without_logs",
        "One canonical valid trace and an explicit empty log collection.",
        1,
        0,
        "PASS",
        _with(**{"TG-TEL-008": "NOT_APPLICABLE"}),
        (TraceSpec("primary"),),
        (),
    ),
    ScenarioDefinition(
        "pass_with_warnings_uncorrelated_logs",
        "One canonical valid trace with one intentionally mismatched log agent.run_id.",
        1,
        2,
        "PASS_WITH_WARNINGS",
        _with(**{"TG-TEL-008": "FAILED"}),
        (TraceSpec("primary"),),
        (LogSpec("correlated-log", "agent.run"), LogSpec("wrong-run-log", "model.call", "mismatch")),
    ),
    ScenarioDefinition(
        "block_fragmented_run",
        "Two canonical valid traces sharing the same agent.run_id to prove fragmentation blocking.",
        2,
        0,
        "BLOCK",
        _with(**{"TG-TEL-003B": "FAILED", "TG-TEL-008": "NOT_APPLICABLE"}),
        (TraceSpec("fragment-a"), TraceSpec("fragment-b")),
        (),
    ),
)


def scenario_catalogue() -> dict[str, object]:
    validate_scenario_catalogue(SCENARIO_DEFINITIONS)
    return {
        "ruleset_version": RULESET_VERSION,
        "scenario_count": len(SCENARIO_DEFINITIONS),
        "scenarios": [item.to_public_dict() for item in SCENARIO_DEFINITIONS],
        "synthetic": True,
        "sanitized": True,
    }


def scenario_catalogue_json() -> str:
    return json.dumps(scenario_catalogue(), indent=2, sort_keys=True) + "\n"


def get_definition(name: str) -> ScenarioDefinition:
    for definition in SCENARIO_DEFINITIONS:
        if definition.name == name:
            return definition
    raise KeyError(name)


def runtime_scenario(definition: ScenarioDefinition, batch_id: str) -> RuntimeScenario:
    scenario_id = f"tg-gate3b-scenario-{uuid4()}"
    return RuntimeScenario(
        definition=definition,
        batch_id=batch_id,
        scenario_id=scenario_id,
        agent_run_id=f"tg-gate3b-run-{uuid4()}",
        log_ids=tuple(f"tg-gate3b-log-{uuid4()}" for _ in definition.log_plan),
    )


def validate_scenario_catalogue(definitions: tuple[ScenarioDefinition, ...] = SCENARIO_DEFINITIONS) -> None:
    if len(definitions) != 4:
        raise ValueError("Gate 3B catalogue must contain exactly four scenarios.")
    names = [item.name for item in definitions]
    if len(set(names)) != len(names):
        raise ValueError("Gate 3B scenario names must be unique.")
    for definition in definitions:
        validate_scenario_definition(definition)


def validate_scenario_definition(definition: ScenarioDefinition) -> None:
    registered = set(RULE_BY_ID)
    actual = set(definition.expected_rule_statuses)
    missing = sorted(registered - actual)
    unknown = sorted(actual - registered)
    if missing or unknown:
        raise ValueError(f"{definition.name} expected statuses must exactly match registered rules; missing={missing}, unknown={unknown}")
    invalid = sorted(rule_id for rule_id, status in definition.expected_rule_statuses.items() if status not in {item.value for item in RuleStatus})
    if invalid:
        raise ValueError(f"{definition.name} has invalid statuses: {invalid}")
    if definition.expected_verdict not in {item.value for item in Verdict}:
        raise ValueError(f"{definition.name} has invalid verdict.")
    implied = _verdict_from_status_map(definition.expected_rule_statuses)
    if implied != definition.expected_verdict:
        raise ValueError(f"{definition.name} declares {definition.expected_verdict} but statuses imply {implied}.")
    if definition.expected_trace_count <= 0 or len(definition.trace_plan) != definition.expected_trace_count:
        raise ValueError(f"{definition.name} has invalid trace count.")
    if definition.expected_log_count < 0 or len(definition.log_plan) != definition.expected_log_count:
        raise ValueError(f"{definition.name} has invalid log count.")
    if definition.name == "block_fragmented_run":
        if definition.expected_trace_count != 2:
            raise ValueError("Fragmented scenario must expect exactly two traces.")
    elif definition.expected_trace_count != 1:
        raise ValueError(f"{definition.name} must expect exactly one trace.")


def _verdict_from_status_map(statuses: dict[str, str]) -> str:
    synthetic = [RULE_BY_ID[rule_id].result(RuleStatus(status), "", observed={}) for rule_id, status in statuses.items()]
    return verdict_from_rule_results(synthetic).value

