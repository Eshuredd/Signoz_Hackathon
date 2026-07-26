from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gate2.models import Source, Span, Trace
from gate3b.config import Gate3BConfig
from gate3b.models import LOG_ID_ATTR, TRACE_BATCH_ATTR, TRACE_SCENARIO_ATTR, TRACE_SCENARIO_NAME_ATTR, RetrievedLog, RuntimeScenario
from gate3b.scenarios import get_definition, runtime_scenario


@pytest.fixture
def config() -> Gate3BConfig:
    return Gate3BConfig("http://localhost:8080", "secret", "http://localhost:4318/v1/traces", "http://localhost:4318/v1/logs", 1, 5, 1, 1, "svc")


@pytest.fixture
def scenario() -> RuntimeScenario:
    return runtime_scenario(get_definition("pass_single_trace_correlated_logs"), "batch-test")


def make_trace(trace_id: str, scenario: RuntimeScenario, root: str = "1" * 16, tool: str = "2" * 16, model: str = "3" * 16) -> Trace:
    now = datetime.now(UTC)
    base = {TRACE_BATCH_ATTR: scenario.batch_id, TRACE_SCENARIO_ATTR: scenario.scenario_id, TRACE_SCENARIO_NAME_ATTR: scenario.name}
    return Trace(
        trace_id,
        [
            Span(trace_id, root, None, "agent.run", now, now, 1, {}, base | {"agent.run_id": scenario.agent_run_id, "agent.name": "traceguard-gate3b", "agent.status": "success"}, {"service.name": "svc"}, "svc"),
            Span(trace_id, tool, root, "tool.call", now, now, 1, {}, base | {"tool.status": "success"}, {"service.name": "svc"}, "svc"),
            Span(trace_id, model, root, "model.call", now, now, 1, {}, base | {"gen_ai.request.model": "gpt-gate3b", "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 2}, {"service.name": "svc"}, "svc"),
        ],
        now,
        Source.TRACE_API,
    )


def make_log(log_id: str, scenario: RuntimeScenario, trace_id: str, span_id: str, run_id: str | None = None, body: str = "body") -> RetrievedLog:
    attrs = {
        LOG_ID_ATTR: log_id,
        TRACE_BATCH_ATTR: scenario.batch_id,
        TRACE_SCENARIO_ATTR: scenario.scenario_id,
        TRACE_SCENARIO_NAME_ATTR: scenario.name,
        "agent.run_id": run_id or scenario.agent_run_id,
    }
    return RetrievedLog(log_id, datetime.now(UTC).isoformat().replace("+00:00", "Z"), trace_id, span_id, body, attrs, {"service.name": "svc"}, "svc")

