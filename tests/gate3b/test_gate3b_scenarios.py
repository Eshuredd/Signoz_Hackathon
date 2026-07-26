from __future__ import annotations

import json
import re

from gate3.rules import RULE_BY_ID
from gate3b.scenarios import SCENARIO_DEFINITIONS, scenario_catalogue, scenario_catalogue_json, validate_scenario_catalogue


def test_catalogue_contract_is_static_complete_and_stable() -> None:
    validate_scenario_catalogue()
    assert len(SCENARIO_DEFINITIONS) == 4
    assert len({item.name for item in SCENARIO_DEFINITIONS}) == 4
    for definition in SCENARIO_DEFINITIONS:
        assert set(definition.expected_rule_statuses) == set(RULE_BY_ID)
        assert len(definition.expected_rule_statuses) == 14
        assert definition.expected_trace_count in {1, 2}
        assert definition.expected_log_count in {0, 2}
    assert scenario_catalogue_json() == scenario_catalogue_json()
    dumped = json.dumps(scenario_catalogue())
    assert "SIGNOZ_API_KEY" not in dumped
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", dumped)

