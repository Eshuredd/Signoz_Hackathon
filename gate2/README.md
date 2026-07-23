# TraceGuard Gate 2

Gate 2 compares two SigNoz retrieval paths for future TraceGuard evaluator input:

- Gate 2A: SigNoz Trace/query API
- Gate 2B: SigNoz MCP

This gate only evaluates retrieval evidence. It does not implement Gate 3, malformed telemetry, the TraceGuard evaluator, TG-TEL/TG-AGT rules, scoring, release decisions, APIs, dashboards, alerts, databases, CI/CD, sample agents, or LLM explanations.

## Current Status

Gate 2A is implemented but requires `SIGNOZ_API_KEY` for live retrieval. In the latest live run on 2026-07-23, SigNoz health and version succeeded, but direct lookup and `agent.run_id` search were not attempted because `SIGNOZ_API_KEY` was `<unset>` in the repository-root `.env`.

Gate 2B is implemented with Streamable HTTP request/notification separation, MCP session ID reuse, SSE event parsing, exact MCP failure-stage preservation, conservative structured search-result parsing, tool discovery from `tools/list`, schema-derived arguments, direct/search workflow independence, and search-to-details retrieval. In the latest live run, MCP health succeeded, but `initialize` failed with HTTP `401` because `SIGNOZ_API_KEY` was `<unset>`.

Current decision for this no-key live run:

```text
HYBRID_REQUIRES_MORE_EVIDENCE
```

Provisional evaluator source for this no-key run: `none`. When a keyed Trace API direct lookup with complete fields succeeds, the Trace API remains the provisional evaluator source unless MCP is fully demonstrated.

Gate 2 is not fully complete yet because authenticated Trace API and MCP telemetry retrieval could not be runtime-tested.

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
- Docker client/server: `29.4.3`
- Docker Compose: `v5.1.4`
- Foundry: `v0.2.15`
- SigNoz: `v0.133.0`, `ee=Y`, `setupCompleted=true`
- MCP image: `signoz/signoz-mcp-server:latest`
- MCP server version from logs: `v0.9.0`
- MCP `/livez`: `ok`
- Docker health for `signoz-mcp`: `unhealthy` because the generated healthcheck invokes `wget`, which is absent in the MCP image; external `/livez` succeeded.

## Service Account

Gate 2 loads local configuration from the repository-root `.env` file. Copy the committed placeholder template and edit the local file:

```bash
cp .env.example .env
```

Set the local values in `.env`:

```bash
SIGNOZ_API_KEY=<your-service-account-key>
SIGNOZ_TRACE_ID=<trace-id>
TRACEGUARD_AGENT_RUN_ID=<run-id>
```

`SIGNOZ_API_KEY` is a SigNoz service-account key with trace read permission. `SIGNOZ_TRACE_ID` must be a 32-character hexadecimal trace ID, and `TRACEGUARD_AGENT_RUN_ID` should correspond to the telemetry being tested.

The repository-root `.env` is ignored by Git. `.env.example` is safe to commit because it contains placeholders only. Shell-exported values override `.env`, so deployed environments should inject secrets through their own secret-management system. Gate 2 intentionally records only whether the API key is `<set>` or `<unset>` and never prints the key.

## Fresh Live Telemetry

Gate 1A generated:

```text
TRACEGUARD_AGENT_RUN_ID=855414bd-3493-4e71-bada-a4bf4b872b26
SIGNOZ_TRACE_ID=c5b74d8d9f7dd08f9ee32af3bf2aa8e6
```

Relationship fixture generated:

```text
TRACEGUARD_GATE2_FIXTURE_RUN_ID=gate2-c74e4df7-1924-4392-a238-aafe707c666a
relationship trace_id=5b14a548f30dd545086c89fec0bfb915
root_span_id=74efe51568e368a6
child_span_id=078f8ca44e7bb9cd
child_parent_span_id=74efe51568e368a6
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

Actual MCP tools discovered in the latest live run: none, because `initialize` failed before `tools/list`.

Actual MCP input schema summaries observed in the latest live run: none, because `tools/list` was not reached.

Latest MCP results:

- Direct lookup: unavailable; details tool was not reached.
- Search-to-details: unavailable; search tool was not reached.
- Relationship retrieval: unavailable; `initialize` failed first.
- Stability check: unavailable; no repeated details retrieval was possible.
- Exact failed stage: `mcp_initialize`.
- Blocker: MCP endpoint was reachable, but `initialize` returned HTTP `401` requiring `Authorization` or `SIGNOZ-API-KEY`.

## Run

Install dependencies:

```bash
python3 -m pip install -r gate2/requirements.txt
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

Latest no-key full comparison exit code: `1`.

Latest Trace API results:

- Health: `ok`.
- Version: `v0.133.0`.
- Direct lookup: unavailable because `SIGNOZ_API_KEY` was `<unset>`.
- `agent.run_id` discovery: unavailable because `SIGNOZ_API_KEY` was `<unset>`.
- Relationship retrieval: unavailable because `SIGNOZ_API_KEY` was `<unset>`.
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
python3 -m pytest tests/gate2 -v
```

Latest result: `86 passed`, `0 failed`, `0 skipped`.
