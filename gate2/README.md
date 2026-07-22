# TraceGuard Gate 2

Gate 2 compares two SigNoz retrieval paths for future TraceGuard evaluator input:

- Gate 2A: SigNoz Trace/query API
- Gate 2B: SigNoz MCP

This gate only evaluates retrieval evidence. It does not implement the TraceGuard evaluator, malformed telemetry, TG-TEL rules, sample agents, dashboards, alerts, databases, APIs, or release gating.

## Status

Gate 2A is implemented as a provisional retrieval path through:

- `POST /api/v4/traces/{trace_id}/waterfall`
- `POST /api/v5/query_range`

The Trace API probe now classifies completeness from normalized field assessments. It reports `complete structured telemetry` and deterministic suitability only when `Trace.has_all_required_fields()` passes. A root-only trace keeps parent-child preservation as `not observed`.

Gate 2B is implemented as an MCP runtime probe, but MCP is not considered authoritative unless an actual structured, stable, multi-span workflow is observed. MCP must expose trace-related tools from `tools/list`, return machine-readable trace telemetry, preserve all required fields, preserve a valid root-child relationship, and return the same structural fields across two equivalent retrievals.

## Official MCP Attempt

Official docs inspected on July 22, 2026:

- SigNoz MCP server: <https://signoz.io/docs/ai/signoz-mcp-server/>
- SigNoz Docker standalone Foundry MCP enablement: <https://signoz.io/docs/install/docker/>
- SigNoz MCP server repository: <https://github.com/SigNoz/signoz-mcp-server>

Supported self-hosted approaches from the docs include Foundry MCP enablement, the official `signoz/signoz-mcp-server` Docker image, the official binary, Go install, or source build. For this repo, the Foundry path was used.

Current non-secret local state observed during this correction:

- `foundryctl version`: `v0.2.15`
- Docker daemon: unavailable at `/Users/dspl_012/.docker/run/docker.sock`
- SigNoz API on `http://localhost:8080`: not reachable during this run
- MCP health on `http://localhost:8000/livez`: not reachable during this run
- `SIGNOZ_API_KEY`, `SIGNOZ_TRACE_ID`, and `TRACEGUARD_AGENT_RUN_ID`: unset in the shell

Supported MCP enablement applied:

```yaml
spec:
  mcp:
    spec:
      enabled: true
```

Command attempted:

```bash
foundryctl cast -f casting.yaml --no-ledger
```

Observed result:

```text
unable to get image 'signoz/signoz-mcp-server:latest': Cannot connect to the Docker daemon at unix:///Users/dspl_012/.docker/run/docker.sock. Is the docker daemon running?
```

This is an installation/startup prerequisite failure. It is not evidence about MCP telemetry completeness. The smallest next action is to start Docker, re-run `foundryctl cast -f casting.yaml`, verify `curl -fsS http://localhost:8000/livez`, export a SigNoz service-account key, and run `python3 gate2/mcp_probe.py`.

## Configure

Do not commit real secrets.

```bash
export SIGNOZ_BASE_URL=http://localhost:8080
export SIGNOZ_API_KEY="<service-account-key>"
export SIGNOZ_TRACE_ID="<trace-id>"
export TRACEGUARD_AGENT_RUN_ID="<agent-run-id>"
export SIGNOZ_MCP_URL=http://localhost:8000/mcp
export SIGNOZ_MCP_HEALTH_URL=http://localhost:8000/livez
```

Optional:

```bash
export SIGNOZ_REQUEST_TIMEOUT_SECONDS=10
export SIGNOZ_DEBUG=false
```

## Relationship Fixture

`relationship_fixture.py` emits one valid two-span trace for Gate 2 retrieval validation:

```text
gate2.test.root
└── gate2.test.child
```

It is not malformed telemetry and is not Gate 3. Use it only when relationship preservation needs to be empirically verified.

```bash
cd gate2
python3 relationship_fixture.py
```

The retrieval check must verify both spans share the same trace ID, the child `parent_span_id` equals the root `span_id`, direct trace retrieval returns both spans, and the normalized model preserves the relationship.

## Run

Install dependencies:

```bash
python3 -m pip install -r gate2/requirements.txt
```

Trace API only:

```bash
cd gate2
python3 signoz_api_client.py
```

MCP only:

```bash
cd gate2
python3 mcp_probe.py
```

Full comparison:

```bash
cd gate2
python3 main.py
```

Full comparison exit codes:

- `0`: both retrieval paths were tested sufficiently and an evidence-based final decision was produced
- `1`: the Trace API failed, no authoritative telemetry source is usable, configuration is invalid, or Gate 2 cannot proceed
- `2`: the Trace API works as the provisional source, but MCP remains unresolved because it could not be runtime-tested

## Evidence

Raw runtime artifacts are written under `gate2/artifacts/` and are ignored by Git.

Sanitized committed evidence lives under `gate2/evidence/`:

- `observed_environment.json`
- `trace_api_field_matrix.json`
- `trace_api_relationship_check.json`
- `mcp_attempt_summary.json`
- `gate2_decision.json`

These files must not contain API keys, authentication tokens, user credentials, or full sensitive telemetry.

## Tests

Unit tests do not require a running SigNoz instance:

```bash
python3 -m pytest tests/gate2
```
