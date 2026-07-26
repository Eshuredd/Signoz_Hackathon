from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


TRACE_BATCH_ATTR = "traceguard.gate3b_batch_id"
TRACE_SCENARIO_ATTR = "traceguard.gate3b_scenario_id"
TRACE_SCENARIO_NAME_ATTR = "traceguard.gate3b_scenario_name"
LOG_ID_ATTR = "traceguard.gate3b_log_id"
RULESET_VERSION = "traceguard-telemetry-v2"
AUTHORITATIVE_TRACE_SOURCE = "TRACE_API_AUTHORITATIVE"
AUTHORITATIVE_LOG_SOURCE = "SIGNOZ_LOG_API"


class Gate3BError(Exception):
    """Base class for Gate 3B errors."""


class Gate3BConfigError(Gate3BError):
    """Configuration or catalogue input is invalid."""


class Gate3BInfrastructureError(Gate3BError):
    """Known live infrastructure failure."""


class Gate3BMismatchError(Gate3BError):
    """Deterministic preservation or expectation mismatch."""


@dataclass(frozen=True)
class TraceSpec:
    name: str


@dataclass(frozen=True)
class LogSpec:
    name: str
    span_name: str
    agent_run_id_mode: str = "match"
    body: str = "synthetic Gate 3B log"


@dataclass(frozen=True)
class ScenarioDefinition:
    name: str
    description: str
    expected_trace_count: int
    expected_log_count: int
    expected_verdict: str
    expected_rule_statuses: dict[str, str]
    trace_plan: tuple[TraceSpec, ...]
    log_plan: tuple[LogSpec, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "expected_trace_count": self.expected_trace_count,
            "expected_log_count": self.expected_log_count,
            "expected_verdict": self.expected_verdict,
            "expected_rule_statuses": dict(sorted(self.expected_rule_statuses.items())),
            "trace_plan": [item.name for item in self.trace_plan],
            "log_plan": [
                {"name": item.name, "span_name": item.span_name, "agent_run_id_mode": item.agent_run_id_mode}
                for item in self.log_plan
            ],
            "synthetic": True,
            "sanitized": True,
        }


@dataclass(frozen=True)
class RuntimeScenario:
    definition: ScenarioDefinition
    batch_id: str
    scenario_id: str
    agent_run_id: str
    log_ids: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.definition.name


@dataclass(frozen=True)
class TraceEmissionResult:
    scenario_name: str
    agent_run_id: str
    emitted_trace_ids: tuple[str, ...]
    root_span_ids_by_trace_id: dict[str, str]
    span_ids_by_trace_id_and_name: dict[str, dict[str, str]]
    parent_span_ids_by_trace_id_and_name: dict[str, dict[str, str | None]]
    expected_attributes_by_trace_id_and_name: dict[str, dict[str, dict[str, object]]]
    exported_at: str
    exported: bool = True

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class LogEmissionResult:
    scenario_name: str
    log_ids: tuple[str, ...]
    expected_agent_run_ids: dict[str, str]
    expected_trace_ids: dict[str, str]
    expected_span_ids: dict[str, str]
    bodies: dict[str, str]
    exported_at: str
    exported: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_name": self.scenario_name,
            "log_ids": list(self.log_ids),
            "expected_agent_run_ids": self.expected_agent_run_ids,
            "expected_trace_ids": self.expected_trace_ids,
            "expected_span_ids": self.expected_span_ids,
            "body_count": len(self.bodies),
            "exported_at": self.exported_at,
            "exported": self.exported,
            "sanitized": True,
        }


@dataclass(frozen=True)
class RetrievedLog:
    log_id: str
    timestamp: str | None
    trace_id: str | None
    span_id: str | None
    body: object
    attributes: dict[str, object]
    resource_attributes: dict[str, object]
    service_name: str | None
    source: str = "SigNoz Logs API"

    def to_dict(self) -> dict[str, object]:
        return {
            "log_id": self.log_id,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "body": self.body,
            "attributes": self.attributes,
            "resource_attributes": self.resource_attributes,
            "service_name": self.service_name,
            "source": self.source,
            "sanitized": True,
        }


@dataclass(frozen=True)
class RetrievalStats:
    search_attempt_count: int
    retrieval_attempt_count: int
    elapsed_ms: int
    last_retry_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "search_attempt_count": self.search_attempt_count,
            "retrieval_attempt_count": self.retrieval_attempt_count,
            "elapsed_ms": self.elapsed_ms,
            "last_retry_reason": self.last_retry_reason,
        }


@dataclass(frozen=True)
class TraceRetrievalResult:
    traces: tuple[object, ...]
    discovered_trace_ids: tuple[str, ...]
    stats: RetrievalStats


@dataclass(frozen=True)
class LogRetrievalResult:
    logs: tuple[RetrievedLog, ...]
    stats: RetrievalStats


@dataclass(frozen=True)
class EnvironmentCheckResult:
    health_ok: bool
    signoz_version: str
    authenticated_trace_api_access: bool
    authenticated_log_api_access: bool
    trace_otlp_endpoint: str
    log_otlp_endpoint: str
    checked_at: str

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class TracePreservationResult:
    trace_ids_match: bool
    span_count_match: bool
    span_names_match: bool
    span_ids_match: bool
    parent_relationships_match: bool
    canonical_attributes_match: bool
    run_id_preserved: bool
    fragmentation_preserved: bool
    scenario_correlation_match: bool
    service_identity_preserved: bool
    timing_preserved: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(
            (
                self.trace_ids_match,
                self.span_count_match,
                self.span_names_match,
                self.span_ids_match,
                self.parent_relationships_match,
                self.canonical_attributes_match,
                self.run_id_preserved,
                self.fragmentation_preserved,
                self.scenario_correlation_match,
                self.service_identity_preserved,
                self.timing_preserved,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy() | {"errors": list(self.errors), "passed": self.passed}


@dataclass(frozen=True)
class LogPreservationResult:
    log_ids_match: bool
    scenario_correlation_match: bool
    trace_span_correlation_match: bool
    agent_run_id_preserved: bool
    intentional_mismatch_preserved: bool
    body_preserved: bool
    timestamp_preserved: bool
    service_identity_preserved: bool
    resource_attributes_preserved: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(
            (
                self.log_ids_match,
                self.scenario_correlation_match,
                self.trace_span_correlation_match,
                self.agent_run_id_preserved,
                self.intentional_mismatch_preserved,
                self.body_preserved,
                self.timestamp_preserved,
                self.service_identity_preserved,
                self.resource_attributes_preserved,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy() | {"errors": list(self.errors), "passed": self.passed}


@dataclass(frozen=True)
class VerificationResult:
    trace_preservation_result: bool
    log_preservation_result: bool
    trace_details: TracePreservationResult | None = None
    log_details: LogPreservationResult | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_preservation_result": self.trace_preservation_result,
            "log_preservation_result": self.log_preservation_result,
            "trace_details": self.trace_details.to_dict() if self.trace_details else None,
            "log_details": self.log_details.to_dict() if self.log_details else None,
            "errors": list(self.errors),
            "passed": self.passed,
        }


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
