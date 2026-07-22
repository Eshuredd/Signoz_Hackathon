# TraceGuard Gate 2

Gate 2 compares two SigNoz retrieval paths for future TraceGuard evaluator input:

- Gate 2A: SigNoz Trace/query API
- Gate 2B: SigNoz MCP

The goal is only to determine whether each source can return complete, structured span data. This does not implement the evaluator, rule engine, API server, dashboard, CLI, database, or any TraceGuard product architecture.

## Observed Local SigNoz

- SigNoz UI/API: `http://localhost:8080`
- Health: `GET /api/v1/health` returned `{"status":"ok"}`
- Version: `GET /api/v1/version` returned `v0.133.0`, `ee=Y`, `setupCompleted=true`
- Trace/search API auth: protected endpoints returned `401 unauthenticated` without `SIGNOZ-API-KEY`
- MCP: `casting.yaml.lock` contains `mcp.enabled: false`, `mcp.status.addresses.http: null`
- MCP health: `http://localhost:8000/livez` was not reachable

Official docs checked:

- [Trace API overview](https://signoz.io/docs/traces-management/trace-api/overview/)
- [Search traces](https://signoz.io/docs/traces-management/trace-api/search-traces/)
- [Service accounts](https://signoz.io/docs/manage/administrator-guide/iam/service-accounts/)
- [SigNoz MCP server](https://signoz.io/docs/ai/signoz-mcp-server/)

## Install

From this directory:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If you already use the repository-level virtual environment:

```bash
source ../.venv/bin/activate
python -m pip install -r requirements.txt
```

## Configure

Copy the example values into your shell. Do not commit real secrets.

```bash
export SIGNOZ_BASE_URL=http://localhost:8080
export SIGNOZ_API_KEY="<service-account-key>"
export SIGNOZ_TRACE_ID="<trace-id-printed-by-gate1>"
export TRACEGUARD_AGENT_RUN_ID="<run-id-printed-by-gate1>"
```

Create `SIGNOZ_API_KEY` in SigNoz:

1. Open `http://localhost:8080`.
2. Go to **Settings > Service Accounts**.
3. Create a service account.
4. Assign a viewer role or another role with trace read access.
5. Open the service account **Keys** tab and create a key.
6. Export that key as `SIGNOZ_API_KEY`.

To get the Gate 1A IDs, run:

```bash
cd ../gate1
python telemetry.py
```

Use the printed values:

```text
TraceGuard Gate 1A run_id=<use-this-for-TRACEGUARD_AGENT_RUN_ID>
SUCCESS: exported one custom trace (... trace_id=<use-this-for-SIGNOZ_TRACE_ID>)
```

Gate 1A now includes both `traceguard.run_id` and `agent.run_id` on new traces so Gate 2 can test the future lookup path.

## Run

Trace API only:

```bash
python signoz_api_client.py
```

MCP only:

```bash
export SIGNOZ_MCP_URL=http://localhost:8000/mcp
export SIGNOZ_MCP_HEALTH_URL=http://localhost:8000/livez
python mcp_probe.py
```

Full comparison:

```bash
python main.py
```

The comparison writes JSON artifacts under `gate2/artifacts/`, including:

- `trace_api_waterfall_raw.json`
- `trace_api_search_raw.json`
- `trace_api_normalized.json`
- `gate2_comparison.json`

Secrets are not written to artifacts; `SIGNOZ_API_KEY` is recorded only as `<set>` or `<unset>`.

## Observed Gate 2 Run

Fresh Gate 1A target used for the local run:

- trace_id: `3f44b12ed6cfd03d3bfd3e29d132674e`
- agent.run_id: `4980418c-985d-4f70-8862-ca43ac569cce`

Trace API results:

- Direct lookup succeeded through `POST /api/v4/traces/{traceID}/waterfall`
- Attribute search succeeded through `POST /api/v5/query_range`
- `agent.run_id` search matched 1 span row
- The normalized trace contained 1 span and all required evaluator fields

MCP results:

- MCP server was not running locally
- `curl -fsS http://localhost:8000/livez` failed to connect
- No MCP tool list or trace response could be observed
- MCP time-box was not exhausted; a concrete local config blocker was found immediately

## Field Matrix

| Required field | Trace API | MCP | Notes |
|---|---|---|---|
| trace_id | present | unavailable | Trace API span had `3f44b12ed6cfd03d3bfd3e29d132674e`; MCP not reachable |
| span_id | present | unavailable | Trace API returned span ID `a7654272e52f60bd` |
| parent_span_id | present | unavailable | Root span used empty string, preserving the field |
| span_name | present | unavailable | `traceguard.gate1.connectivity` |
| start_time | present | unavailable | Normalized from SigNoz span timestamp |
| end_time | present | unavailable | Computed from start time plus `duration_nano` |
| duration | present | unavailable | Stored as `duration_nano` |
| status | present | unavailable | Trace API returned `status_code`, `status_code_string`, and `has_error` |
| complete attributes | present | unavailable | Included `agent.run_id`, `traceguard.run_id`, `traceguard.project`, `traceguard.gate`, and `traceguard.check` |
| resource attributes | present | unavailable | Included `service.name`, `service.version`, `service.instance.id`, and OpenTelemetry SDK metadata |

## Decision

Final Gate 2 recommendation:

```text
HYBRID_REQUIRES_MORE_EVIDENCE
```

Reason: the Trace API path was empirically complete for the Gate 1A trace, but MCP could not be runtime-tested because the local Foundry-installed SigNoz stack has MCP disabled and no MCP HTTP endpoint. Until MCP is enabled and shown to return complete structured span data, the SigNoz Trace API is the provisional evaluator data source for later TraceGuard work.

Smallest MCP unblock:

```text
Enable or install the SigNoz MCP server for the local Foundry stack, expose its HTTP /mcp and /livez endpoints, and provide a service account API key.
```

Operational rule for later gates: proceed as though the SigNoz Trace API is authoritative unless stronger MCP evidence is collected.
