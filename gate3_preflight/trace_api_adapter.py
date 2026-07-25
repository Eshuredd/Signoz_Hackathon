from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

GATE2_DIR = Path(__file__).resolve().parents[1] / "gate2"
if str(GATE2_DIR) not in sys.path:
    sys.path.insert(0, str(GATE2_DIR))

from exceptions import (  # type: ignore[import-not-found]
    AuthenticationFailure,
    AuthorizationFailure,
    ConfigurationError,
    ConnectionFailure,
    EmptySearchResults,
    InvalidResponseSchema,
    RequestTimeout,
    TraceNotFound,
    UnsupportedAPIOperation,
)
from gate2.config import Gate2Config
from gate2.logging_config import configure_logging
from gate2.models import Trace
from gate2.signoz_api_client import SigNozAPIClient

from .config import PreflightConfig


class PreflightRetrievalError(Exception):
    """Raised when live preflight retrieval cannot prove the required contract."""


@dataclass(frozen=True)
class PreflightEnvironmentCheck:
    health_ok: bool
    health_response_summary: dict[str, object]
    version_ok: bool
    signoz_version: str
    authenticated_trace_api_access: bool
    checked_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "health_ok": self.health_ok,
            "health_response_summary": self.health_response_summary,
            "version_ok": self.version_ok,
            "signoz_version": self.signoz_version,
            "authenticated_trace_api_access": self.authenticated_trace_api_access,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class RetrievalResult:
    trace: Trace
    discovered_trace_ids: tuple[str, ...]
    search_attempt_count: int
    retrieval_attempt_count: int
    elapsed_ms: int
    last_retry_reason: str | None = None


class PreflightTraceAPIAdapter:
    def __init__(self, client: SigNozAPIClient) -> None:
        self.client = client

    def check_health(self) -> dict[str, object]:
        return self.client.health_check()

    def read_version(self) -> dict[str, object]:
        return self.client.version()

    def verify_authenticated_trace_api_access(self) -> bool:
        try:
            self.client.search_traces(
                {"traceguard.preflight_id": f"auth-check-{datetime.now(UTC).timestamp()}"},
                start_time=datetime.now(UTC) - timedelta(minutes=1),
                end_time=datetime.now(UTC),
                limit=1,
            )
            return True
        except EmptySearchResults:
            return True

    def run_environment_check(self) -> PreflightEnvironmentCheck:
        health = self.check_health()
        health_ok = health.get("status") == "ok"
        if not health_ok:
            raise InvalidResponseSchema("SigNoz health response did not report status=ok.")
        version = self.read_version()
        signoz_version = _extract_version(version)
        access = self.verify_authenticated_trace_api_access()
        return PreflightEnvironmentCheck(
            health_ok=health_ok,
            health_response_summary=_health_summary(health),
            version_ok=True,
            signoz_version=signoz_version,
            authenticated_trace_api_access=access,
            checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )


def client_from_preflight_config(config: PreflightConfig) -> SigNozAPIClient:
    gate2_config = Gate2Config(
        signoz_base_url=config.signoz_base_url,
        signoz_trace_id=None,
        agent_run_id=None,
        signoz_api_key=config.signoz_api_key,
        request_timeout_seconds=config.request_timeout_seconds,
        debug=False,
        mcp_url="http://localhost:8000/mcp",
        mcp_health_url="http://localhost:8000/livez",
    )
    return SigNozAPIClient(gate2_config, configure_logging(False))


def poll_and_retrieve(
    client: SigNozAPIClient,
    *,
    preflight_id: str,
    emitted_trace_id: str,
    timeout_seconds: float,
    interval_seconds: float,
    expected_span_count: int = 3,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> RetrievalResult:
    start = monotonic()
    deadline = start + timeout_seconds
    search_attempts = 0
    retrieval_attempts = 0
    last_retry_reason: str | None = None
    discovered_trace_ids: tuple[str, ...] = ()
    searched_successfully = False

    while True:
        now = monotonic()
        if now > deadline:
            break
        try:
            search_attempts += 1
            hits, _raw = client.search_traces(
                {"traceguard.preflight_id": preflight_id},
                start_time=datetime.now(UTC) - timedelta(minutes=15),
                end_time=datetime.now(UTC) + timedelta(minutes=5),
                limit=20,
            )
            searched_successfully = True
            discovered_trace_ids = tuple(sorted({hit.trace_id for hit in hits}))
            if len(discovered_trace_ids) != 1:
                raise PreflightRetrievalError(f"Expected exactly one logical trace, observed {len(discovered_trace_ids)}.")
            if discovered_trace_ids[0] != emitted_trace_id:
                raise PreflightRetrievalError("Discovered trace ID did not match emitted trace ID.")
            retrieval_attempts += 1
            trace, _raw_trace = client.get_trace(discovered_trace_ids[0])
            if trace.trace_id != emitted_trace_id:
                raise PreflightRetrievalError("Retrieved trace ID did not match emitted trace ID.")
            retry_reason = _readiness_retry_reason(trace, expected_span_count, preflight_id)
            if retry_reason is None:
                return RetrievalResult(
                    trace=trace,
                    discovered_trace_ids=discovered_trace_ids,
                    search_attempt_count=search_attempts,
                    retrieval_attempt_count=retrieval_attempts,
                    elapsed_ms=int((monotonic() - start) * 1000),
                    last_retry_reason=last_retry_reason,
                )
            last_retry_reason = retry_reason
        except EmptySearchResults:
            last_retry_reason = "empty_search_results"
        except TraceNotFound:
            if not searched_successfully:
                raise
            last_retry_reason = "trace_not_found_after_search_hit"
        except (AuthenticationFailure, AuthorizationFailure, ConfigurationError, InvalidResponseSchema, UnsupportedAPIOperation, ConnectionFailure, RequestTimeout):
            raise

        now = monotonic()
        if now >= deadline:
            break
        sleep_for = min(interval_seconds, deadline - now)
        if sleep_for > 0:
            sleeper(sleep_for)

    elapsed_ms = int((monotonic() - start) * 1000)
    raise PreflightRetrievalError(f"Timed out waiting for SigNoz ingestion readiness: {last_retry_reason}; elapsed_ms={elapsed_ms}")


def _readiness_retry_reason(trace: Trace, expected_span_count: int, preflight_id: str) -> str | None:
    if len(trace.spans) < expected_span_count:
        return "retrieved_trace_incomplete_span_count"
    missing_correlation = [
        span.span_id
        for span in trace.spans
        if span.span_name in {"agent.run", "tool.call", "model.call"}
        and span.attributes.get("traceguard.preflight_id") != preflight_id
    ]
    if missing_correlation:
        return "retrieved_trace_missing_preflight_correlation"
    return None


def _health_summary(health: dict[str, object]) -> dict[str, object]:
    return {
        "status": health.get("status"),
        "ok": health.get("status") == "ok",
    }


def _extract_version(version: dict[str, object]) -> str:
    raw = version.get("version") or version.get("tag") or version.get("buildVersion")
    return str(raw) if raw else "unknown"
