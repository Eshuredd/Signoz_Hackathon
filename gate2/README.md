# TraceGuard Gate 2

Gate 2 compares two SigNoz retrieval paths for future TraceGuard evaluator input:

- Gate 2A: SigNoz Trace/query API
- Gate 2B: SigNoz MCP

This gate only evaluates retrieval evidence. It does not implement Gate 3, malformed telemetry, the TraceGuard evaluator, TG-TEL/TG-AGT rules, scoring, release decisions, APIs, dashboards, alerts, databases, CI/CD, sample agents, or LLM explanations.

## Current Status

Gate 2A is implemented and now loads the latest Gate 1 trace context from `.traceguard/runtime/latest_gate1.json` when the dynamic ID overrides are blank. In the latest live run on 2026-07-25, Gate 2 loaded the manifest source correctly, `SIGNOZ_API_KEY` was `<set>`, the service-account self-check succeeded, direct trace lookup succeeded, and `agent.run_id` search resolved the same trace.

Gate 2B is implemented with Streamable HTTP request/notification separation, MCP session ID reuse, SSE event parsing, exact MCP failure-stage preservation, conservative structured search-result parsing, tool discovery from `tools/list`, schema-derived arguments, direct/search workflow independence, search-to-details retrieval, and SigNoz query-row normalization. In the latest live run, MCP health, initialize, initialized notification, `tools/list`, direct lookup, repeated direct lookup, and relationship direct lookup succeeded. MCP remained non-authoritative because the normalized payload did not expose complete custom span attributes such as `agent.run_id`, so search-to-details run ID validation failed.

Current decision for this live run:

```text
TRACE_API_AUTHORITATIVE
```

Provisional evaluator source for this run: `SigNoz Trace API`.

Gate 2 is complete for this phase: Trace API is the authoritative retrieval source, while MCP is meaningfully tested but incomplete for evaluator-grade telemetry.

## Live Commands

Docker and Foundry:

```bash
open -a Docker
docker version
docker info
docker compose version
foundryctl cast -f casting.yaml --no-ledger
```

Service checks:

```bash
curl -fsS http://localhost:8080/api/v1/health
curl -fsS http://localhost:8080/api/v1/version
curl -fsS http://localhost:8000/livez
docker compose -f pours/deployment/compose.yaml ps
docker logs --tail 120 signoz-mcp
```

Observed live state:

- Docker daemon available: yes
- Docker client/server: `28.4.0`
- Docker Compose: `v2.39.4-desktop.1`
- SigNoz: `v0.134.0`, `ee=Y`, `setupCompleted=true`
- MCP image: `signoz/signoz-mcp-server:latest`
- MCP server version from logs: `main-f6086b3`
- MCP `/livez`: `ok`
- Docker health for `signoz-mcp`: `unhealthy` because the generated healthcheck invokes `wget`, which is absent in the MCP image; external `/livez` succeeded.
- The repository compose ingester was initially replaced by OpAMP with `nop` pipelines because SigNoz had no org. Gate 1 live proof was completed by running the same collector image with the committed static collector config and without OpAMP manager config.

## Service Account And Runtime IDs

Gate 2 loads stable local configuration from the repository-root `.env` file. Copy the committed placeholder template and edit the local file once:

```bash
cp .env.example .env
```

Set stable local values in `.env`:

```bash
SIGNOZ_API_KEY=<your-service-account-key>
```

`SIGNOZ_API_KEY` is a SigNoz service-account key with trace read permission.

Gate 1 writes fresh dynamic IDs to:

```text
.traceguard/runtime/latest_gate1.json
```

Gate 2 automatically reads that latest successful Gate 1 runtime manifest when `SIGNOZ_TRACE_ID` and `TRACEGUARD_AGENT_RUN_ID` are blank or absent. You should not edit `.env` after every Gate 1 run.

If your local `.env` already contains older `SIGNOZ_TRACE_ID` and `TRACEGUARD_AGENT_RUN_ID` values, blank or remove those two lines once to enable the no-argument manifest workflow. Keep `SIGNOZ_API_KEY` and the stable SigNoz URLs in `.env`.

The repository-root `.env` is ignored by Git. `.env.example` is safe to commit because it contains placeholders only. Shell-exported values override `.env`, so deployed environments should inject secrets through their own secret-management system. Gate 2 intentionally records only whether the API key is `<set>` or `<unset>` and never prints the key.

Trace-context precedence is:

1. Explicit process environment variables or values loaded from `.env`
2. Latest successful Gate 1 runtime manifest
3. Not configured

If overriding dynamic IDs explicitly, provide both values from the same Gate 1 execution:

```bash
SIGNOZ_TRACE_ID=<trace-id> \
TRACEGUARD_AGENT_RUN_ID=<run-id> \
python3 gate2/main.py
```

Providing only one explicit ID raises a configuration error rather than mixing it with an unrelated manifest value.

## Fresh Live Telemetry

Gate 1A generated:

```text
first successful run_id=8848eb4c-c23a-44fd-ab7e-f23958c4bd77
first successful trace_id=2fed5f0ffbd62b3be751af910e89c5e0
latest run_id=2f93baa3-b17f-4fe0-97be-80fb646f3110
latest trace_id=c625f09f0672d266d7a28ba4da41db1f
```

The latest successful Gate 1 run replaced `.traceguard/runtime/latest_gate1.json`, and Gate 2 loaded that run with `trace_context_source=manifest`.

Relationship fixture generated:

```text
TRACEGUARD_GATE2_FIXTURE_RUN_ID=gate2-621e7840-891d-4c02-92fd-910997ae6721
relationship trace_id=d9fb43d0f9b5dcadef12edae0b40e543
root_span_id=1bf72c5ea6f3833a
child_span_id=b51c032a9593f0b0
child_parent_span_id=1bf72c5ea6f3833a
```

The relationship fixture is valid two-span telemetry:

```text
gate2.test.root
└── gate2.test.child
```

It is not malformed telemetry and is not Gate 3. Relationship preservation becomes `observed` only after the source being evaluated retrieves both spans back from SigNoz and preserves the child `parent_span_id`.

## MCP Behavior

The MCP client now handles Streamable HTTP as separate flows:

- JSON-RPC requests with an `id` require a JSON-RPC JSON/SSE response.
- JSON-RPC notifications without an `id` accept HTTP `202` with an empty body.
- `Mcp-Session-Id` is captured from initialize responses and reused on later requests and notifications, but the session value is not exposed in sanitized evidence.
- SSE parsing combines complete event `data:` lines and rejects responses with no valid JSON-RPC object.

The MCP search workflow is:

1. Discover compatible trace tools from `tools/list`.
2. Call the selected trace-search tool using arguments derived from its actual `inputSchema`.
3. Extract candidate trace IDs only from structured fields such as `trace_id`, `traceId`, or `traceID`.
4. Prefer a candidate matching requested `agent.run_id` when attributes permit validation.
5. Call the selected trace-details tool with that trace ID.
6. Normalize the details response.
7. Confirm the normalized trace contains requested `agent.run_id` when attributes are available.
8. Repeat the equivalent details retrieval and compare the same logical trace for stability.

Actual trace-related MCP tools discovered in the latest live run:

- `signoz_aggregate_traces`
- `signoz_get_trace_details`
- `signoz_search_traces`

Selected trace tools:

- details: `signoz_get_trace_details`
- search: `signoz_search_traces`

Observed input schema summary:

- `signoz_get_trace_details`: required `traceId`; optional `start`, `end`, `timeRange`, `includeSpans`, `searchContext`
- `signoz_search_traces`: optional `start`, `end`, `timeRange`, `filter`, `service`, `operation`, `error`, `minDuration`, `maxDuration`, `limit`, `offset`, `searchContext`

Latest MCP results:

- Direct lookup: observed; trace-details normalized 1 span from the latest Gate 1 manifest trace.
- Search-to-details: failed; the normalized trace did not expose the requested `agent.run_id` in available attributes.
- Relationship retrieval: observed through direct lookup; both fixture spans were normalized and the child `parent_span_id` was preserved.
- Stability check: observed; repeated direct details retrieval returned stable structural fields.
- Exact failed stage: `mcp_normalization`.
- Blocker: none external. MCP is incomplete because custom span attributes are absent or transformed in the returned row payload.

## Run

Install dependencies:

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r gate1\requirements.txt -r gate2\requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Equivalent POSIX commands:

```bash
python3 -m pip install -r gate2/requirements.txt
```

Normal local workflow:

```bash
python3 gate1/telemetry.py
python3 gate2/main.py
```

Trace API probe:

```bash
python3 gate2/signoz_api_client.py
```

MCP probe:

```bash
python3 gate2/mcp_probe.py
```

Full comparison:

```bash
python3 gate2/main.py
echo $?
```

Latest full comparison exit code: `0`.

Latest Trace API results:

- Health: `ok`.
- Version: `v0.134.0`.
- Trace context source: `manifest`.
- Direct lookup: observed; retrieved 1 span from the latest Gate 1 manifest trace.
- `agent.run_id` discovery: observed; matched 1 span row for the manifest run ID.
- Relationship retrieval: observed; retrieved 2 fixture spans and preserved the child `parent_span_id`.
- Response classification: complete structured telemetry.

Exit codes:

- `0`: both retrieval paths were sufficiently runtime-tested and a final evidence-based source decision was produced
- `1`: Trace API is unusable, configuration is invalid, or no authoritative/provisional source can be used
- `2`: Trace API works provisionally, but MCP could not be sufficiently runtime-tested because of an external blocker

## Decision Logic

`MCP_CAN_BE_AUTHORITATIVE` requires complete structured fields, an observed retrieval workflow, validated multiple-span parent-child evidence, stable repeated retrieval of the same logical trace, machine-readable responses, and no unresolved MCP errors.

`TRACE_API_AUTHORITATIVE` applies when MCP is reachable and meaningfully tested but returns incomplete, unstable, or otherwise non-evaluator-grade telemetry.

`HYBRID_REQUIRES_MORE_EVIDENCE` applies when MCP cannot be sufficiently runtime-tested because of an external setup, availability, authentication, or compatibility blocker.

## Evidence

Raw runtime artifacts are written under `gate2/artifacts/` and are ignored by Git.

Sanitized committed evidence lives under `gate2/evidence/`, including:

- `observed_environment.json`
- `trace_api_field_matrix.json`
- `trace_api_workflow_check.json`
- `trace_api_relationship_check.json`
- `mcp_attempt_summary.json`
- `mcp_tool_inventory.json`
- `mcp_field_matrix.json`
- `mcp_relationship_check.json`
- `mcp_stability_check.json`
- `gate2_decision.json`

## Tests

Unit tests do not require a running SigNoz instance:

```bash
.\.venv\Scripts\python.exe -m pytest tests\runtime -v --basetemp .test-tmp-runtime -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests\gate1 -v --basetemp .test-tmp-gate1 -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests\gate2\test_config.py -v --basetemp .test-tmp-config -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests\gate2 -v --basetemp .test-tmp-gate2 -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest -v --basetemp .test-tmp-all -p no:cacheprovider
```

Latest results:

- runtime: `15 passed`, `0 failed`, `0 skipped`
- Gate 1: `7 passed`, `0 failed`, `0 skipped`
- Gate 2 config: `26 passed`, `0 failed`, `0 skipped`
- all Gate 2: `97 passed`, `0 failed`, `0 skipped`
- full suite: `119 passed`, `0 failed`, `0 skipped`
