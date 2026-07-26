from __future__ import annotations

from gate3b.log_api_adapter import normalize_log_row
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

