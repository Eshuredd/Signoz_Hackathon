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

Runtime artifacts are written under `.traceguard/runtime/gate3b/<batch_id>/` and are ignored. Raw SigNoz responses, headers, cookies, bearer tokens, and API keys are not written. Normal runner commands do not write committed evidence and do not decide `gate3b_complete`.

## Evidence Finalization

Committed evidence is published only by the explicit finalizer:

```powershell
.\.venv\Scripts\python.exe gate3b\finalize_evidence.py --batch-id <batch_id> --dry-run
.\.venv\Scripts\python.exe gate3b\finalize_evidence.py --batch-id <batch_id>
```

`--dry-run` validates the selected runtime batch, runs the verification commands and tracked-secret scan, and prints the proposed evidence without writing `gate3b/evidence/`.

The finalizer rejects unknown batches, environment-only summaries, selected-scenario runs, incomplete runs, failed live runs, fewer than four scenarios, mismatched status maps or verdicts, and preservation failures. It never alters the runtime source summary.

Finalizer exit codes:

- `0`: evidence finalized and committed evidence files written
- `1`: runtime batch exists but fails the Gate 3B completion contract
- `2`: invalid CLI usage, missing batch, invalid summary, or malformed input
- `3`: required verification command or tracked-secret scan failed
- `4`: unexpected internal error or evidence-writing failure

`gate3b_complete` is decided only by the finalizer. Completion requires a complete four-scenario live batch with live exit code `0`, exact 14-rule status-map matches, verdict matches, trace/log preservation, Gate 3B/Gate 3/Gate 3 preflight/Gate 2/Gate 1/runtime/full-suite tests, Gate 3 fixture validation, Gate 3 evaluate-all, and the tracked-secret scan.

The finalizer records concise sanitized command results with command name, current-Python command, exit code, pass/fail, parsed pytest count where available, output summaries, and capture time. It does not fabricate test totals.

Published evidence files:

- `gate3b_scenario_catalog.json`: static scenario catalogue, expected verdicts, counts, and complete status maps
- `gate3b_log_api_contract.json`: SigNoz logs query contract, normalized fields, OpenTelemetry compatibility contract, and Gate 2 public query method
- `gate3b_live_results.json`: selected full-batch live summary and preservation summaries
- `gate3b_verification_results.json`: actual finalizer verification command results
- `gate3b_secret_scan.json`: tracked-file scan count and finding count, with no secret values
- `gate3b_decision.json`: final Gate 3B completion decision and next action

## Preservation Hardening

Trace preservation verifies exact trace ID sets, duplicate trace objects, three canonical span names, exact emitted/retrieved span IDs, parent maps, canonical attributes, Gate 3B correlation attributes, service identity, timezone-aware timing, and fragmented-run independence. The fragmented scenario must retain two independent roots sharing the same expected `agent.run_id`.

Log preservation verifies exact log ID sets, Gate 3B log/scenario correlation attributes, trace/span membership in the retrieved traces, correlated `agent.run_id` values, the intentionally wrong warning-scenario `agent.run_id`, exact body preservation, timezone-aware timestamps, service identity, and non-empty resource attributes including `service.name`.

Temporarily incomplete expected log rows are classified as `TransientIncompleteLogRow` and retried until the configured monotonic deadline. Permanent invalid schemas, authentication/authorization/configuration failures, unsupported API operation, connection failures, request timeouts, unexpected log IDs, contradictory duplicate log IDs, and scenario-ID mismatches are not retried.

OpenTelemetry log API imports are isolated in `gate3b/otel_log_compat.py`. The module records installed OpenTelemetry package versions and whether a private fallback import path was required. Gate 3B log retrieval uses Gate 2's public `SigNozAPIClient.query_range()` method and does not call `_request_json()` directly.
