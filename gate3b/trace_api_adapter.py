from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

GATE2_DIR = Path(__file__).resolve().parents[1] / "gate2"
if str(GATE2_DIR) not in sys.path:
    sys.path.insert(0, str(GATE2_DIR))

from exceptions import AuthenticationFailure, AuthorizationFailure, ConfigurationError, ConnectionFailure, EmptySearchResults, InvalidResponseSchema, RequestTimeout, TraceNotFound, UnsupportedAPIOperation  # type: ignore[import-not-found]
from gate2.config import Gate2Config
from gate2.logging_config import configure_logging
from gate2.signoz_api_client import SigNozAPIClient

from .config import Gate3BConfig
from .models import TRACE_SCENARIO_ATTR, EnvironmentCheckResult, Gate3BInfrastructureError, RetrievalStats, RuntimeScenario, TraceRetrievalResult, now_iso


NON_RETRY = (AuthenticationFailure, AuthorizationFailure, ConfigurationError, InvalidResponseSchema, UnsupportedAPIOperation, ConnectionFailure, RequestTimeout)


class Gate3BTraceRetrievalError(Gate3BInfrastructureError):
    """Trace retrieval failed."""


def client_from_config(config: Gate3BConfig) -> SigNozAPIClient:
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


def verify_trace_api_access(client: SigNozAPIClient) -> bool:
    try:
        client.search_traces({TRACE_SCENARIO_ATTR: f"auth-check-{datetime.now(UTC).timestamp()}"}, start_time=datetime.now(UTC) - timedelta(minutes=1), end_time=datetime.now(UTC), limit=1)
        return True
    except EmptySearchResults:
        return True


def poll_and_retrieve_traces(
    client: SigNozAPIClient,
    scenario: RuntimeScenario,
    emitted_trace_ids: tuple[str, ...],
    *,
    timeout_seconds: float,
    interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> TraceRetrievalResult:
    start = monotonic()
    deadline = start + timeout_seconds
    search_attempts = 0
    retrieval_attempts = 0
    last_retry_reason: str | None = None
    expected = set(emitted_trace_ids)
    while monotonic() <= deadline:
        try:
            search_attempts += 1
            hits, _raw = client.search_traces({TRACE_SCENARIO_ATTR: scenario.scenario_id}, start_time=datetime.now(UTC) - timedelta(minutes=15), end_time=datetime.now(UTC) + timedelta(minutes=5), limit=50)
            discovered = tuple(sorted({hit.trace_id for hit in hits}))
            discovered_set = set(discovered)
            if len(discovered_set - expected) > 0:
                raise Gate3BTraceRetrievalError("Unexpected trace IDs discovered for Gate 3B scenario.")
            if len(discovered) > scenario.definition.expected_trace_count:
                raise Gate3BTraceRetrievalError("Unexpected trace count greater than scenario contract.")
            if discovered_set != expected:
                last_retry_reason = "missing_expected_trace_ids"
                raise _Retry()
            traces = []
            for trace_id in discovered:
                retrieval_attempts += 1
                trace, _raw_trace = client.get_trace(trace_id)
                if trace.trace_id != trace_id or trace_id not in expected:
                    raise Gate3BTraceRetrievalError("Retrieved trace ID mismatch.")
                reason = _readiness_retry_reason(trace, scenario.scenario_id)
                if reason:
                    last_retry_reason = reason
                    raise _Retry()
                traces.append(trace)
            return TraceRetrievalResult(tuple(traces), discovered, RetrievalStats(search_attempts, retrieval_attempts, int((monotonic() - start) * 1000), last_retry_reason))
        except EmptySearchResults:
            last_retry_reason = "empty_search_results"
        except TraceNotFound:
            last_retry_reason = "trace_not_found_after_search_hit"
        except _Retry:
            pass
        except NON_RETRY:
            raise
        if monotonic() >= deadline:
            break
        sleeper(min(interval_seconds, max(0.0, deadline - monotonic())))
    raise Gate3BTraceRetrievalError(f"Timed out waiting for trace ingestion: {last_retry_reason}; elapsed_ms={int((monotonic() - start) * 1000)}")


class _Retry(Exception):
    pass


def _readiness_retry_reason(trace: object, scenario_id: str) -> str | None:
    spans = getattr(trace, "spans", ())
    if len(spans) < 3:
        return "retrieved_trace_incomplete_span_count"
    for span in spans:
        if getattr(span, "span_name", None) in {"agent.run", "tool.call", "model.call"} and getattr(span, "attributes", {}).get(TRACE_SCENARIO_ATTR) != scenario_id:
            return "retrieved_trace_missing_gate3b_correlation"
    return None

