# Gate 3 Live Preflight

`gate3_preflight` is the network-enabled proof that canonical TraceGuard fields survive OTLP export, SigNoz ingestion, Trace API search, direct waterfall retrieval, normalization, schema validation, and Gate 3 evaluation.

It intentionally sits outside `gate3/` so the deterministic evaluator remains network-independent. It reuses Gate 2 Trace API retrieval because Gate 2 selected `TRACE_API_AUTHORITATIVE`; MCP is not used.

Two scenarios are emitted:

`canonical_valid` emits `agent.run`, `tool.call`, and `model.call` with all canonical required attributes and expects `PASS`.

`canonical_incomplete` emits the same three-span structure while omitting approved canonical fields and expects the corrected evaluator to block or warn according to `traceguard-telemetry-v2`.

Search uses `traceguard.preflight_id` only as a synthetic correlation attribute. Runtime artifacts are written under `.traceguard/runtime/gate3_preflight/<batch_id>/` and are ignored by Git. Raw API responses, API keys, headers, cookies, and bearer tokens must not be committed or printed.

Run:

```powershell
python gate3_preflight/main.py
```

Completion requires both scenarios to be exported, discovered, retrieved, structurally preserved, normalized, schema validated, evaluated, and matched against expected per-rule statuses.
