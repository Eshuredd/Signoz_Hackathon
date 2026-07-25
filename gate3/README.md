# TraceGuard Gate 3A

Gate 3A evaluates one already-normalized trace and answers whether the telemetry is complete and structurally trustworthy enough for deterministic evaluation.

Gate 2 remains responsible for authentication, retrieval, source selection, and normalization. The established Gate 2 decision is `TRACE_API_AUTHORITATIVE`: future live normalized telemetry should come from the SigNoz Trace API. Gate 3A does not reopen that source-selection decision.

Gate 3A is deliberately offline. It reads normalized JSON, validates the input envelope, applies deterministic telemetry-completeness rules, and emits structured findings plus one final verdict. It does not know about SigNoz API keys, MCP sessions, HTTP endpoints, Docker, collector configuration, databases, dashboards, webhooks, or deployment.

## Input Contract

Gate 3A consumes a versioned envelope:

```json
{
  "schema_version": 1,
  "trace": {
    "trace_id": "0123456789abcdef0123456789abcdef",
    "spans": [],
    "retrieved_at": "2026-07-25T08:00:02Z",
    "source": "fixture",
    "metadata": {}
  }
}
```

Malformed JSON or schema-invalid envelopes raise `TraceInputError`. That is not the same as incomplete telemetry. A valid trace with missing span fields can produce `WARN` or `BLOCK` findings; malformed input is rejected before evaluation so it is not disguised as a telemetry verdict.

## Verdicts

Verdict precedence is exact:

- `BLOCK`: at least one blocking finding exists.
- `WARN`: no blocking findings exist, but at least one warning finding exists.
- `PASS`: no blocking or warning findings exist.

There is no numerical score, weighting, or severity average in Gate 3A. A single blocking condition must remain a block.

## Rule Catalogue

Rules run in ascending rule-ID order, are side-effect-free, deterministic, and never perform network access.

| Rule ID | Name | Severity | Purpose |
| --- | --- | --- | --- |
| TG-TEL-001 | TRACE_HAS_SPANS | BLOCKING | Require at least one span in the normalized trace. |
| TG-TEL-002 | REQUIRED_SPAN_IDENTITY | BLOCKING | Require trace_id, span_id, and span_name on every span. |
| TG-TEL-003 | TRACE_ID_CONSISTENCY | BLOCKING | Require span trace IDs to match the enclosing trace ID. |
| TG-TEL-004 | UNIQUE_SPAN_IDS | BLOCKING | Require unique non-empty span IDs within one trace. |
| TG-TEL-005 | SINGLE_ROOT_SPAN | BLOCKING | Require exactly one root span when spans exist. |
| TG-TEL-006 | PARENT_REFERENCE_INTEGRITY | BLOCKING | Require non-root parent_span_id values to resolve within the trace. |
| TG-TEL-007 | REQUIRED_TIMING_FIELDS | BLOCKING | Require start_time, end_time, and duration_nano on every span. |
| TG-TEL-008 | VALID_TIMING_ORDER | BLOCKING | Reject negative durations and end_time values before start_time. |
| TG-TEL-009 | SERVICE_IDENTITY | WARNING | Require service identity from service_name or resource service.name. |
| TG-TEL-010 | AGENT_RUN_CORRELATION | BLOCKING | Require root agent.run_id for external run correlation. |
| TG-TEL-011 | TRACEGUARD_RUN_CORRELATION | WARNING | Warn when root traceguard.run_id is absent. |
| TG-TEL-012 | RUN_ID_CONSISTENCY | BLOCKING | Require run ID attributes to be internally consistent. |
| TG-TEL-013 | TRACEGUARD_CONTEXT | WARNING | Warn when root TraceGuard project or gate context is absent. |

TG-AGT agent-behaviour reliability rules are deferred. Gate 3A first proves that the telemetry substrate is complete and deterministic before judging agent behaviour.

## Fixtures

The committed fixture corpus is synthetic and non-secret:

- `fixtures/valid`: traces expected to pass.
- `fixtures/warn`: traces expected to warn only.
- `fixtures/block`: traces expected to block, including one block-plus-warning precedence case.

Independent expected results live in `expected/fixture_expectations.json`. Tests verify that every fixture has one expectation, every expectation references an existing fixture, expected rule IDs exist, and all results match.

## CLI

Default output is structured JSON.

```powershell
.\.venv\Scripts\python.exe gate3\cli.py evaluate gate3\fixtures\valid\valid_single_span.json
.\.venv\Scripts\python.exe gate3\cli.py evaluate-all gate3\fixtures
.\.venv\Scripts\python.exe gate3\cli.py validate-fixtures gate3\fixtures
```

Evaluation exit codes:

- `0`: `PASS`
- `10`: `WARN`
- `20`: `BLOCK`
- `2`: invalid CLI usage or invalid input
- `3`: internal evaluator failure

`evaluate-all` exit codes:

- `0`: all actual results match expected fixture results
- `1`: one or more fixture results do not match expectations
- `2`: invalid fixture or expectation manifest

## Tests

Gate 3A tests are offline and do not require SigNoz:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\gate3 -v --basetemp .test-tmp-gate3 -p no:cacheprovider
.\.venv\Scripts\python.exe gate3\cli.py validate-fixtures gate3\fixtures
.\.venv\Scripts\python.exe gate3\cli.py evaluate-all gate3\fixtures
```

No live Trace API retrieval, MCP retrieval, `.env` loading, or network access is part of Gate 3A.
