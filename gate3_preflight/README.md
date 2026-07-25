# Gate 3 Live Preflight

`gate3_preflight` is the network-enabled proof that canonical TraceGuard fields survive OTLP export, SigNoz ingestion, Trace API search, direct waterfall retrieval, normalization, schema validation, and Gate 3 evaluation.

It intentionally sits outside `gate3/` so the deterministic evaluator remains network-independent. It reuses Gate 2 Trace API retrieval because Gate 2 selected `TRACE_API_AUTHORITATIVE`; MCP is not used.

Two scenarios are emitted:

`canonical_valid` emits `agent.run`, `tool.call`, and `model.call` with all canonical required attributes and expects `PASS`.

`canonical_incomplete` emits the same three-span structure while preserving trace IDs, span IDs, parent relationships, timing, service identity, `agent.run_id`, and `traceguard.preflight_id`. It intentionally omits `agent.name`, `agent.status`, `tool.status`, `gen_ai.request.model`, and both token fields, and expects `BLOCK`.

Each scenario carries a complete 14-rule status map for `traceguard-telemetry-v2`. The runner requires exact status-map equality, exact verdict equality, no duplicate rule IDs, trace-level evaluation, and the expected ruleset version.

Export is verified in two phases. First, an in-memory span exporter captures the locally completed spans and proves there are exactly three spans, one `agent.run` root, one shared trace ID, unique non-empty span IDs, and the declared `tool.call`/`model.call` parent links. Only then are those completed spans passed directly to the OTLP exporter. `SpanExportResult.SUCCESS` and a successful OTLP `force_flush` are required before a trace is reported as emitted.

OTLP HTTP endpoint configuration is normalized with URL parsing. Bare collector bases such as `http://localhost:4318`, root paths, `/v1`, and `/v1/` resolve to `/v1/traces`; existing `/v1/traces` paths are preserved without duplicating path segments. Query strings and fragments are rejected because the exporter contract for them is not relied on here.

Before export, the runner reuses the Gate 2 SigNoz Trace API client to check health, read version, and verify authenticated Trace API access with the configured key. Health must match the Gate 2 `status=ok` contract. Version is recorded as a stable string when exposed, or `unknown` when the endpoint succeeds without one. Authenticated Trace API access is proven with a unique synthetic search attribute; `EmptySearchResults` means the authenticated request was accepted. The key is never written; non-secret snapshots report only `"<set>"`.

Retrieval uses bounded `time.monotonic()` polling. It retries empty search results, temporary `TraceNotFound` after a search hit, temporarily incomplete span count, and temporary absence of `traceguard.preflight_id` on expected spans. It does not retry authentication, authorization, configuration, unsupported API, invalid schema, connection, timeout, or trace-ID mismatch failures.

Preservation verification is implemented in `verification.py`. It checks retrieved trace identity, exact three-span names, span ID preservation, parent-child structure, `traceguard.preflight_id`, required canonical attributes, intentional absences, service identity, and timing before the evaluator result is trusted.

Search uses `traceguard.preflight_id` only as a synthetic correlation attribute. Runtime artifacts are written under `.traceguard/runtime/gate3_preflight/<batch_id>/` and are ignored by Git. Raw API responses, API keys, headers, cookies, and bearer tokens must not be committed or printed.

Runtime artifact layout:

```text
.traceguard/runtime/gate3_preflight/<batch_id>/
  environment_check.json
  emission_manifest.json
  retrieved/<scenario>.normalized.json
  verification/<scenario>.json
  evaluations/<scenario>.json
  preflight_summary.json
```

Commands:

```powershell
python gate3_preflight/main.py
python gate3_preflight/main.py --check-environment
python gate3_preflight/main.py --list-scenarios
```

The default command runs the complete live preflight. `--check-environment` performs only configuration validation, SigNoz health, SigNoz version, and authenticated Trace API verification; it does not construct scenarios, export telemetry, poll, retrieve, or evaluate. `--list-scenarios` prints a stable sanitized JSON catalogue and does not require `SIGNOZ_API_KEY` or network access. Unsupported arguments are rejected by argparse with exit code `2` before live configuration is loaded.

Exit codes are `0` for complete success, `1` for preservation or expectation mismatch after retrieval, `2` for invalid configuration, invalid scenario catalogue configuration, or CLI usage, `3` for known infrastructure failures such as health/authentication/authorization/connection/timeout/unsupported API/schema/OTLP/retrieval failures, and `4` for unexpected internal preflight defects or required artifact-writing failures. Error output includes the stage, exception class, sanitized message, and exit code; it does not include API keys, headers, raw responses, cookies, bearer tokens, or stack traces by default.

Offline tests cover endpoint normalization, CLI parsing, scenario listing stability, environment-check structure, polling retry and non-retry branches, deterministic timeout boundaries, preservation verification booleans, exporter local-span validation and shutdown behavior, and runner exit codes. Tests inject fake clients, exporters, clocks, sleepers, evaluators, and artifact writers so they perform no live network calls.

Local SigNoz restoration must use the existing environment safely. Inspect Docker/Docker Compose status, existing containers, compose files, health/version endpoints, OTLP reachability, and environment-variable presence without printing secrets. Start existing stopped services when safe; do not delete volumes, reset users, rotate keys, edit SigNoz databases, or replace collector configuration unless a specific non-destructive fix is proven necessary.

Completion requires both scenarios to be exported, discovered, retrieved, preserved, normalized, schema validated, evaluated, and matched against expected per-rule statuses and verdicts. `contract_realign_complete` must remain false until live exit code is `0`, both scenarios pass preservation checks, all required tests pass, and evidence is sanitized.
