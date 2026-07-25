from __future__ import annotations

import time
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

GATE2_DIR = Path(__file__).resolve().parents[1] / "gate2"
if str(GATE2_DIR) not in sys.path:
    sys.path.insert(0, str(GATE2_DIR))

from gate2.config import Gate2Config
from gate2.logging_config import configure_logging
from gate2.models import Trace
from gate2.signoz_api_client import EmptySearchResults, SigNozAPIClient

from .config import PreflightConfig


class PreflightRetrievalError(Exception):
    """Raised when live preflight retrieval cannot prove the required contract."""


@dataclass(frozen=True)
class RetrievalResult:
    trace: Trace
    discovered_trace_ids: tuple[str, ...]


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
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> RetrievalResult:
    deadline = monotonic() + timeout_seconds
    last_empty: Exception | None = None
    while monotonic() < deadline:
        try:
            hits, _raw = client.search_traces(
                {"traceguard.preflight_id": preflight_id},
                start_time=datetime.now(UTC) - timedelta(minutes=15),
                end_time=datetime.now(UTC) + timedelta(minutes=5),
                limit=20,
            )
            trace_ids = tuple(sorted({hit.trace_id for hit in hits}))
            if len(trace_ids) != 1:
                raise PreflightRetrievalError(f"Expected exactly one logical trace, observed {len(trace_ids)}.")
            if trace_ids[0] != emitted_trace_id:
                raise PreflightRetrievalError("Discovered trace ID did not match emitted trace ID.")
            trace, _raw_trace = client.get_trace(trace_ids[0])
            if trace.trace_id != emitted_trace_id:
                raise PreflightRetrievalError("Retrieved trace ID did not match emitted trace ID.")
            return RetrievalResult(trace=trace, discovered_trace_ids=trace_ids)
        except EmptySearchResults as exc:
            last_empty = exc
            sleeper(interval_seconds)
    raise PreflightRetrievalError(f"Timed out waiting for SigNoz indexing: {last_empty}")
