from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate3.evaluator import evaluate_trace
from gate3.trace_loader import load_trace_payload

from gate3_preflight.bridge import gate2_trace_to_gate3_envelope
from gate3_preflight.config import PreflightConfig
from gate3_preflight.exporter import emit_scenario
from gate3_preflight.scenarios import scenarios
from gate3_preflight.trace_api_adapter import client_from_preflight_config, poll_and_retrieve


def main() -> int:
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    runtime = Path(".traceguard") / "runtime" / "gate3_preflight" / batch_id
    config = PreflightConfig.from_env()
    client = client_from_preflight_config(config)
    summary: dict[str, object] = {"batch_id": batch_id, "sanitized": True, "config": config.non_secret_snapshot(), "scenarios": {}}
    exit_code = 0
    for scenario in scenarios():
        scenario_summary: dict[str, object] = {}
        try:
            emission = emit_scenario(scenario, config)
            scenario_summary["emitted_trace_id"] = emission.trace_id
            scenario_summary["emitted_span_ids"] = emission.span_ids
            retrieved = poll_and_retrieve(
                client,
                preflight_id=scenario.preflight_id,
                emitted_trace_id=emission.trace_id,
                timeout_seconds=config.poll_timeout_seconds,
                interval_seconds=config.poll_interval_seconds,
            )
            envelope = gate2_trace_to_gate3_envelope(retrieved.trace)
            trace = load_trace_payload(envelope)
            evaluation = evaluate_trace(trace)
            _write_json(runtime / "retrieved" / f"{scenario.name}.normalized.json", envelope)
            _write_json(runtime / "evaluations" / f"{scenario.name}.json", evaluation.to_dict())
            statuses = {item.rule_id: item.status.value for item in evaluation.rule_results}
            expected_matches = evaluation.verdict.value == scenario.expected_verdict and all(statuses.get(k) == v for k, v in scenario.expected_statuses.items())
            span_count_ok = len(trace.spans) == 3
            scenario_summary.update({
                "discovered_trace_ids": list(retrieved.discovered_trace_ids),
                "retrieved_trace_id": retrieved.trace.trace_id,
                "span_count": len(trace.spans),
                "span_count_ok": span_count_ok,
                "expected_verdict": scenario.expected_verdict,
                "actual_verdict": evaluation.verdict.value,
                "expected_rule_statuses": scenario.expected_statuses,
                "actual_rule_statuses": statuses,
                "matched_expectations": expected_matches and span_count_ok,
            })
            summary["scenarios"][scenario.name] = scenario_summary
            if not expected_matches or not span_count_ok:
                exit_code = 1
        except Exception as exc:
            scenario_summary.update({"error_type": exc.__class__.__name__, "message": str(exc)})
            summary["scenarios"][scenario.name] = scenario_summary
            exit_code = 1
    _write_json(runtime / "emission_manifest.json", summary)
    _write_json(runtime / "preflight_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
