# TraceGuard Gate 2

Gate 2 compares two SigNoz retrieval paths for future TraceGuard evaluator input:

- Gate 2A: SigNoz Trace/query API
- Gate 2B: SigNoz MCP

This gate only evaluates retrieval evidence. It does not implement Gate 3, malformed telemetry, the TraceGuard evaluator, TG-TEL/TG-AGT rules, scoring, release decisions, APIs, dashboards, alerts, databases, CI/CD, sample agents, or LLM explanations.

## Current Status

Gate 2A is implemented and now loads the latest Gate 1 trace context from `.traceguard/runtime/latest_gate1.json` when the dynamic ID overrides are blank. In the latest live run on 2026-07-25, Gate 2 loaded the manifest source correctly and `SIGNOZ_API_KEY` was `<set>`, but localhost SigNoz rejected direct lookup and `agent.run_id` search as `AuthenticationFailure: unauthenticated`.

Gate 2B is implemented with Streamable HTTP request/notification separation, MCP session ID reuse, SSE event parsing, exact MCP failure-stage preservation, conservative structured search-result parsing, tool discovery from `tools/list`, schema-derived arguments, direct/search workflow independence, and search-to-details retrieval. In the latest live run, MCP health, initialize, initialized notification, and `tools/list` succeeded. The server exposed `signoz_get_trace_details` and `signoz_search_traces`, but trace tool calls returned upstream unauthenticated responses from SigNoz.

Current decision for this live run:

```text
HYBRID_REQUIRES_MORE_EVIDENCE
```

Provisional evaluator source for this run: `none`. When a valid local service-account key allows Trace API direct lookup with complete fields, the Trace API remains the provisional evaluator source unless MCP is fully demonstrated.

Gate 2 is not fully complete yet because authenticated Trace API telemetry retrieval is still rejected by the local SigNoz instance.

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
latest run_id=b777c572-9a86-41b6-80df-006e1f7bdff1
latest trace_id=8f33780518edab055f88e9a0b4be27a3
```

The latest successful Gate 1 run replaced `.traceguard/runtime/latest_gate1.json`, and Gate 2 loaded that run with `trace_context_source=manifest`.

Relationship fixture generated:

```text
TRACEGUARD_GATE2_FIXTURE_RUN_ID=gate2-b777ae46-5bfa-45af-9a9b-eeaa244b87f7
relationship trace_id=020ad9603fcc933b4769fe2c3efc8466
root_span_id=07fb3ad378ac9998
child_span_id=cac3067162644836
child_parent_span_id=07fb3ad378ac9998
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

- Direct lookup: failed; trace-details tool returned no structured trace object because upstream SigNoz returned unauthenticated.
- Search-to-details: failed; search response did not contain a supported structured result container because upstream SigNoz returned unauthenticated.
- Relationship retrieval: not observed; trace tool calls did not return structured telemetry.
- Stability check: unavailable; no repeated details retrieval was possible.
- Exact failed stage: `mcp_search_result_parsing`.
- Blocker: MCP endpoint was reachable and tool discovery succeeded, but SigNoz rejected upstream trace retrieval as unauthenticated.

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

Latest full comparison exit code: `1`.

Latest Trace API results:

- Health: `ok`.
- Version: `v0.134.0`.
- Trace context source: `manifest`.
- Direct lookup: failed with `AuthenticationFailure: unauthenticated`.
- `agent.run_id` discovery: failed with `AuthenticationFailure: unauthenticated`.
- Relationship retrieval: not observed because authenticated Trace API retrieval failed.
- Response classification: not observed.

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
- all Gate 2: `95 passed`, `0 failed`, `0 skipped`
- full suite: `117 passed`, `0 failed`, `0 skipped`
