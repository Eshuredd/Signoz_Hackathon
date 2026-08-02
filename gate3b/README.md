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

Runtime artifacts are written under `.traceguard/runtime/gate3b/<batch_id>/` and are ignored. Batch IDs use the exact generated format `YYYYMMDDTHHMMSSZ-<12 lowercase hex characters>`. Raw SigNoz responses, headers, cookies, bearer tokens, and API keys are not written. Normal runner commands do not write committed evidence and do not decide `gate3b_complete`.

Live Gate 3B execution is source-bound. The runner records `source_commit_sha` and `source_worktree_clean` in the runtime summary and refuses to export telemetry from a dirty working tree.

## Evidence Finalization

Committed evidence is published only by the explicit finalizer:

```powershell
.\.venv\Scripts\python.exe gate3b\finalize_evidence.py --batch-id <batch_id> --dry-run
.\.venv\Scripts\python.exe gate3b\finalize_evidence.py --batch-id <batch_id>
```

`--dry-run` validates the selected runtime batch, runs the verification commands and tracked-secret scan, and prints the proposed evidence without writing `gate3b/evidence/`.

The finalizer rejects unknown batches, environment-only summaries, selected-scenario runs, incomplete runs, failed live runs, fewer than four scenarios, mismatched status maps or verdicts, and preservation failures. It never alters the runtime source summary.

The finalizer is an independent evidence-trust boundary. Runner conclusion flags such as `matched_expectations`, `exact_status_match`, `verdict_match`, preservation booleans, `matched_count`, `failed_count`, and `all_expectations_matched` are comparison data only and are not trusted as authoritative. For each scenario, the finalizer reloads the raw runtime artifacts under `.traceguard/runtime/gate3b/<batch_id>/`: emission manifests, normalized retrieved traces, normalized retrieved logs, stored run bundle, stored evaluation, stored verification result, scenario catalogue, environment check, and summary.

Scenario truth is recomputed from those artifacts. The finalizer first validates the requested batch ID, resolves the runtime batch directory as a direct child of `RUNTIME_ROOT`, rejects symlinked batch directories, subdirectories, JSON artifacts, normalized traces, and normalized logs, and requires every artifact path to remain inside the selected batch. It reconstructs the trace and log emission contracts, parses retrieved traces/logs into typed objects, re-runs `gate3b.verification.verify_preservation`, rebuilds the Gate 3 run bundle, loads it with `gate3.trace_loader.load_run_bundle_payload`, and re-runs the unchanged `gate3.evaluator.evaluate_run_bundle`. It then compares recomputed preservation details, trace/log counts, ID sets, status maps, verdicts, and aggregate counts against immutable scenario definitions and against the runner artifacts. Any contradiction rejects finalization.

The finalizer validates the runner's non-secret config snapshot, including endpoints, positive timeouts, poll interval bounds, and `SIGNOZ_API_KEY="<set>"`. Trace and log emission service names must equal `TRACEGUARD_GATE3B_SERVICE_NAME`, and retrieved logs must preserve the exact `SigNoz Logs API` source.

Trace emission manifests must have internally consistent trace IDs, root span IDs, canonical span-name maps, parent maps, span IDs, and expected correlation attributes. Log emission manifests must have internally consistent log IDs, trace/span references into the emitted traces, body counts, and expected `agent.run_id` mismatch counts for the warning scenario.

Environment evidence is loaded from `environment_check.json`, cross-checked against `gate3b_summary.json`, and schema/endpoint/authentication-success fields are validated. The finalizer does not repeat live authenticated SigNoz health, trace API, or log API requests, so committed evidence records `environment_evidence_source="runner_observed_and_finalizer_cross_checked"` and `environment_live_checks_repeated_by_finalizer=false`.

The finalizer verifies Git provenance before publication using `git rev-parse --show-toplevel`, `git rev-parse HEAD`, and `git status --porcelain` with argument arrays and `shell=False`. Runtime `source_commit_sha` must match finalizer `HEAD`, runtime `source_worktree_clean` must be true, the repository root must resolve to this repository, and the finalizer working tree must be clean before evidence bytes are written.

Finalizer exit codes:

- `0`: evidence finalized and committed evidence files written
- `1`: runtime batch exists but fails the Gate 3B completion contract
- `2`: invalid CLI usage, missing batch, invalid summary, or malformed input
- `3`: required verification command or tracked-secret scan failed
- `4`: unexpected internal error or evidence-writing failure

`gate3b_complete` is decided only by the finalizer. Completion requires a complete four-scenario live batch with live exit code `0`, exact 14-rule status-map matches, verdict matches, trace/log preservation, Gate 3B/Gate 3/Gate 3 preflight/Gate 2/Gate 1/runtime/full-suite tests, Gate 3 fixture validation, Gate 3 evaluate-all, and the tracked-secret scan.

The finalizer records concise sanitized command results with command name, current-Python command, exit code, pass/fail, parsed pytest count where available, output summaries, and capture time. It does not fabricate test totals.

The verification result set must contain exactly `pip_check`, `compileall`, `gate3b_tests`, `gate3_tests`, `gate3_preflight_tests`, `gate2_tests`, `gate1_tests`, `runtime_tests`, `full_suite`, `gate3_validate_fixtures`, and `gate3_evaluate_all`. Missing, duplicate, unknown, malformed, or failed command results reject finalization.

Secret scanning uses exact placeholder values only, including `<redacted>`, `<set>`, `<your-api-key>`, `<your-service-account-key>`, `example-key`, `fake-key`, `fake-token`, `fake-dotenv-secret`, `synthetic-token`, `test-api-key`, and `changeme-for-local-testing`. Broad words such as `fake`, `example`, `secret`, or `synthetic`, and regex-like characters such as brackets, parentheses, braces, or backslashes, do not exempt a value. The scanner detects environment assignment, colon-style, and JSON credential forms for SigNoz API keys, authorization headers, cookies, passwords, and service-account fields. Cookie/header patterns are line-bounded, and findings never include matched values. Proposed evidence JSON is serialized deterministically once, the exact serialized text is scanned, and those same bytes are written after a clean scan; credible findings block publication without printing matched values. Publication prepares all temporary files before replacement, backs up old targets, removes newly created targets on failure, restores backups, removes scratch files, and verifies target bytes after success.

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

Trace and log emission manifests record the configured emitted service name. Retrieved span and log service identity must exactly match that emitted value; a non-empty but different service name fails preservation.

Temporarily incomplete expected log rows are classified as `TransientIncompleteLogRow` and retried until the configured monotonic deadline. Missing, empty, and recognised-but-unparseable timestamps retry as incomplete indexing. Boolean, dictionary, list, or other structured timestamp values are permanent schema errors. Permanent invalid schemas, authentication/authorization/configuration failures, unsupported API operation, connection failures, request timeouts, unexpected log IDs, contradictory duplicate log IDs, and scenario-ID mismatches are not retried.

OpenTelemetry log API imports are isolated in `gate3b/otel_log_compat.py`. The module attempts known public import paths first, falls back to the tested private paths only when needed, and records selected paths, attempted public paths, attempted private paths, installed OpenTelemetry package versions, and whether any private fallback was required. Gate 3B log retrieval uses Gate 2's public `SigNozAPIClient.query_range()` method and does not call `_request_json()` directly.

Decision evidence includes `source_commit_sha`, `finalizer_commit_sha`, `scenario_validation_recomputed_by_finalizer`, `runner_scenario_conclusion_flags_trusted_as_authoritative`, `runtime_path_confined`, `symlink_protection_verified`, `config_service_name_binding_verified`, `log_source_provenance_verified`, `emission_manifest_consistency_verified`, `json_secret_patterns_verified`, `environment_evidence_source`, `environment_live_checks_repeated_by_finalizer`, `exact_scanned_bytes_written`, `atomic_evidence_publication_succeeded`, `exact_verification_command_set`, and `proposed_evidence_scan_passed` so completion can be audited from recomputed finalizer evidence rather than runner claims.
