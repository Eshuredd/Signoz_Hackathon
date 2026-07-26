from __future__ import annotations

from types import SimpleNamespace

import pytest

from exceptions import AuthenticationFailure, InvalidResponseSchema
from gate3b.log_api_adapter import Gate3BLogRetrievalError, TransientIncompleteLogRow, normalize_log_row, poll_and_retrieve_logs
from gate3b.models import LOG_ID_ATTR, TRACE_SCENARIO_ATTR


def test_normalize_log_row_accepts_signoz_v0134_typed_attribute_maps() -> None:
    row = {
        "data": {
            "attributes_string": {
                LOG_ID_ATTR: "log-1",
                TRACE_SCENARIO_ATTR: "scenario-1",
                "agent.run_id": "run-1",
                "trace_id": "a" * 32,
                "span_id": "1" * 16,
            },
            "attributes_number": {"code.line.number": 10},
            "attributes_bool": {},
            "resources_string": {"service.name": "svc"},
            "body": "synthetic body",
            "trace_id": "",
            "span_id": "",
            "timestamp": 1785042143510280704,
        }
    }
    log = normalize_log_row(row)
    assert log.log_id == "log-1"
    assert log.trace_id == "a" * 32
    assert log.span_id == "1" * 16
    assert log.attributes["agent.run_id"] == "run-1"
    assert log.resource_attributes["service.name"] == "svc"
    assert log.service_name == "svc"


def test_missing_complete_fields_are_transient() -> None:
    with pytest.raises(TransientIncompleteLogRow) as exc:
        normalize_log_row({"data": {"attributes_string": {TRACE_SCENARIO_ATTR: "scenario"}, "body": "body", "timestamp": 1}})
    assert exc.value.reason == "incomplete_log_row_missing_log_id"


def complete_row(scenario, log_id: str = "log-1", timestamp=1):
    return {
        "data": {
            "attributes_string": {LOG_ID_ATTR: log_id, TRACE_SCENARIO_ATTR: scenario.scenario_id, "trace_id": "a" * 32, "span_id": "1" * 16},
            "resources_string": {"service.name": "svc"},
            "body": "body",
            "timestamp": timestamp,
        }
    }


def test_invalid_recognized_timestamps_are_transient_and_structured_types_are_permanent(scenario) -> None:
    for timestamp, reason in ((None, "incomplete_log_row_missing_timestamp"), ("", "incomplete_log_row_missing_timestamp"), ("indexing", "incomplete_log_row_invalid_timestamp")):
        row = complete_row(scenario, timestamp=timestamp)
        with pytest.raises(TransientIncompleteLogRow) as exc:
            normalize_log_row(row)
        assert exc.value.reason == reason
    for timestamp in (True, {}, []):
        with pytest.raises(InvalidResponseSchema):
            normalize_log_row(complete_row(scenario, timestamp=timestamp))
    assert normalize_log_row(complete_row(scenario)).timestamp is not None


def test_permanent_invalid_log_row_schema_fails() -> None:
    with pytest.raises(InvalidResponseSchema):
        normalize_log_row({"data": {"attributes_string": "bad"}})


def test_poll_retries_transient_row_then_succeeds(scenario) -> None:
    log_id = scenario.log_ids[0]
    calls = {"count": 0}

    class Client:
        def query_range(self, payload):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"status": "success", "data": {"data": {"results": [{"rows": [{"data": {"attributes_string": {TRACE_SCENARIO_ATTR: scenario.scenario_id}, "body": "body", "timestamp": 1}}]}]}}}
            return {
                "status": "success",
                "data": {
                    "data": {
                        "results": [
                            {
                                "rows": [
                                    {
                                        "data": {
                                            "attributes_string": {LOG_ID_ATTR: log_id, TRACE_SCENARIO_ATTR: scenario.scenario_id, "trace_id": "a" * 32, "span_id": "1" * 16},
                                            "resources_string": {"service.name": "svc"},
                                            "body": "body",
                                            "timestamp": 1,
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                },
            }

    clock = {"value": 0.0}

    def monotonic() -> float:
        clock["value"] += 0.1
        return clock["value"]

    result = poll_and_retrieve_logs(Client(), scenario, (log_id,), timeout_seconds=1, interval_seconds=0, monotonic=monotonic, sleeper=lambda _: None)
    assert result.logs[0].log_id == log_id
    assert result.stats.last_retry_reason == "incomplete_log_row_missing_log_id"


def test_poll_does_not_retry_authentication_failure(scenario) -> None:
    class Client:
        def query_range(self, payload):
            raise AuthenticationFailure("no")

    with pytest.raises(AuthenticationFailure):
        poll_and_retrieve_logs(Client(), scenario, scenario.log_ids, timeout_seconds=1, interval_seconds=0, monotonic=lambda: 0.0, sleeper=lambda _: None)


def test_poll_unexpected_log_id_is_not_retried(scenario) -> None:
    class Client:
        def query_range(self, payload):
            return {
                "status": "success",
                "data": {
                    "data": {
                        "results": [
                            {
                                "rows": [
                                    {
                                        "data": {
                                            "attributes_string": {LOG_ID_ATTR: "unexpected", TRACE_SCENARIO_ATTR: scenario.scenario_id, "trace_id": "a" * 32, "span_id": "1" * 16},
                                            "resources_string": {"service.name": "svc"},
                                            "body": "body",
                                            "timestamp": 1,
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                },
            }

    with pytest.raises(Gate3BLogRetrievalError):
        poll_and_retrieve_logs(Client(), scenario, scenario.log_ids, timeout_seconds=1, interval_seconds=0, monotonic=lambda: 0.0, sleeper=lambda _: None)
