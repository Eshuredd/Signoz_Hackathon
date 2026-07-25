# Gate 3 Live Preflight

`gate3_preflight` is the network-enabled proof that canonical TraceGuard fields survive OTLP export, SigNoz ingestion, Trace API search, direct waterfall retrieval, normalization, schema validation, and Gate 3 evaluation.

It intentionally sits outside `gate3/` so the deterministic evaluator remains network-independent. It reuses Gate 2 Trace API retrieval because Gate 2 selected `TRACE_API_AUTHORITATIVE`; MCP is not used.

Two scenarios are emitted:

`canonical_valid` emits `agent.run`, `tool.call`, and `model.call` with all canonical required attributes and expects `PASS`.

`canonical_incomplete` emits the same three-span structure while preserving trace IDs, span IDs, parent relationships, timing, service identity, `agent.run_id`, and `traceguard.preflight_id`. It intentionally omits `agent.name`, `agent.status`, `tool.status`, `gen_ai.request.model`, and both token fields, and expects `BLOCK`.

Each scenario carries a complete 14-rule status map for `traceguard-telemetry-v2`. The runner requires exact status-map equality, exact verdict equality, no duplicate rule IDs, trace-level evaluation, and the expected ruleset version.

Export is verified in two phases. First, an in-memory span exporter captures the locally completed spans and proves there are exactly three spans, one `agent.run` root, one shared trace ID, unique non-empty span IDs, and the declared `tool.call`/`model.call` parent links. Only then are those completed spans passed directly to the OTLP exporter. `SpanExportResult.SUCCESS` and a successful OTLP `force_flush` are required before a trace is reported as emitted.

Before export, the runner reuses the Gate 2 SigNoz Trace API client to check health, read version, and verify authenticated Trace API access with the configured key. The key is never written; non-secret snapshots report only `"<set>"`.

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

Run:

```powershell
python gate3_preflight/main.py
```

Exit codes are `0` for complete success, `1` for preservation or expectation mismatch after retrieval, `2` for invalid configuration or CLI usage, `3` for health/authentication/OTLP/retrieval/timeout infrastructure failure, and `4` for unexpected internal preflight defects.

Completion requires both scenarios to be exported, discovered, retrieved, preserved, normalized, schema validated, evaluated, and matched against expected per-rule statuses and verdicts. `contract_realign_complete` must remain false until live exit code is `0`, both scenarios pass preservation checks, all required tests pass, and evidence is sanitized.
