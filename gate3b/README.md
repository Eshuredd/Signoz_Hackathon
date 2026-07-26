# Gate 3B: Live Run-Level Telemetry Validation

Gate 3B proves that TraceGuard can make deterministic run-level quality decisions from synthetic OpenTelemetry traces and logs retrieved from the real SigNoz environment.

Pipeline:

`OpenTelemetry traces/logs -> OTLP export -> SigNoz ingestion -> Trace API and logs API retrieval -> normalized run bundle -> gate3.trace_loader.load_run_bundle_payload -> unchanged gate3.evaluator.evaluate_run_bundle`

Gate 3B uses `traceguard-telemetry-v2`. Gate 2 remains the source of Trace API authentication and trace retrieval behavior, with `TRACE_API_AUTHORITATIVE` as the authoritative trace source. Gate 3 remains network-independent and owns the canonical evaluator. TG-AGT agent-behaviour rules are intentionally deferred.

## Scenarios

`pass_single_trace_correlated_logs` emits one canonical three-span trace and two correlated logs. Expected verdict: `PASS`.

`pass_single_trace_without_logs` emits one canonical trace and no logs. Expected `TG-TEL-008`: `NOT_APPLICABLE`; verdict: `PASS`.

`pass_with_warnings_uncorrelated_logs` emits one canonical trace and two logs, one with an intentionally wrong `agent.run_id`. Expected `TG-TEL-008`: `FAILED`; verdict: `PASS_WITH_WARNINGS`.

`block_fragmented_run` emits two canonical traces sharing one `agent.run_id`. Expected `TG-TEL-003B`: `FAILED`; verdict: `BLOCK`.

All emitted spans and logs carry `traceguard.gate3b_batch_id`, `traceguard.gate3b_scenario_id`, and `traceguard.gate3b_scenario_name`. Logs also carry `traceguard.gate3b_log_id`.

## CLI

```powershell
.\.venv\Scripts\python.exe gate3b\main.py --list-scenarios
.\.venv\Scripts\python.exe gate3b\main.py --check-environment
.\.venv\Scripts\python.exe gate3b\main.py --scenario pass_single_trace_correlated_logs
.\.venv\Scripts\python.exe gate3b\main.py
```

Exit codes:

- `0`: selected scenarios matched preservation checks, exact status maps, and verdicts
- `1`: infrastructure completed but preservation, status, or verdict expectations mismatched
- `2`: invalid CLI input, configuration, or catalogue
- `3`: known environment/infrastructure failure
- `4`: unexpected internal failure or artifact-writing failure

Failure precedence is `4 > 3 > 2 > 1 > 0`.

Runtime artifacts are written under `.traceguard/runtime/gate3b/<batch_id>/` and are ignored. Raw SigNoz responses, headers, cookies, bearer tokens, and API keys are not written. Committed evidence lives under `gate3b/evidence/` and is sanitized. `gate3b_complete` is set to `true` only after all four live scenarios complete and exactly match.

