# TraceGuard Telemetry Contract v1

TraceGuard evaluates whether agent telemetry is complete enough for deterministic audit decisions. The corrected ruleset version is `traceguard-telemetry-v2`; the earlier `tg-tel-v1` prototype is superseded because it assigned canonical `TG-TEL-*` IDs to structural checks.

## Canonical Attributes

The canonical root span is `agent.run`. Its required non-empty attributes are `agent.run_id`, `agent.name`, and `agent.status`.

Tool spans are spans named `tool.call`, spans whose name begins with `tool.`, or spans where `attributes["gen_ai.operation.name"] == "execute_tool"`.

Model spans are spans named `model.call`, spans whose name begins with `model.`, spans containing `gen_ai.request.model`, or spans whose `gen_ai.operation.name` represents a model invocation.

`traceguard.run_id`, `traceguard.project`, and `traceguard.gate` are not required input telemetry attributes. They may appear as metadata, but they do not affect the canonical evaluator verdict.

## Evaluation Levels

Trace-level evaluation accepts a single normalized trace envelope and evaluates all trace-level rules plus explicit `NOT_APPLICABLE` results for run-level rules.

Run-level evaluation accepts a run bundle with `agent_run_id`, traces, logs, and metadata. It aggregates trace-level rules across supplied traces and evaluates run-level trace fragmentation and log correlation.

## Root Definition

A root span has `parent_span_id` equal to `null`, an empty string, or sixteen hexadecimal zeroes. `TG-TEL-001` requires exactly one root span and requires its name to be exactly `agent.run`.

## Verdict Policy

Public verdicts are `PASS`, `PASS_WITH_WARNINGS`, and `BLOCK`. The human label for `PASS_WITH_WARNINGS` is `PASS WITH WARNINGS`.

`BLOCK` wins when any blocking rule fails or any rule returns `EVALUATION_ERROR`. `PASS_WITH_WARNINGS` applies when there are no blocking failures and at least one warning rule fails. `PASS` applies when no rule fails. `NOT_APPLICABLE` does not lower the verdict.

## Rule Status Policy

Every registered rule produces exactly one result with status `PASSED`, `FAILED`, `NOT_APPLICABLE`, or `EVALUATION_ERROR`. Results are ordered by namespace and rule ID. Rule implementation exceptions are captured as `EVALUATION_ERROR` without stack traces unless debug mode is enabled.

## Schema-Version Policy

Trace inputs use `SUPPORTED_TRACE_INPUT_SCHEMA_VERSION = 1`. Expectation manifests use `SUPPORTED_EXPECTATION_SCHEMA_VERSION = 1`. Run bundles use `SUPPORTED_RUN_BUNDLE_SCHEMA_VERSION = 1`. Schema versions must be real integers; booleans are invalid.

## Rule-ID Stability

Rule IDs are public audit identifiers. Once a stable rule ID is assigned a meaning, that meaning must not change. Historical `tg-tel-v1` evidence remains in Git history, but repository-head reports use `traceguard-telemetry-v2`.

## Canonical TG-TEL Rules

### TG-TEL-001

`AGENT_RUN_ROOT`, blocking, trace-level. Requires exactly one root span named `agent.run`. Evidence includes root count, root IDs, root names, and the expected name.

### TG-TEL-002

`AGENT_RUN_REQUIRED_ATTRIBUTES`, blocking, trace-level. Requires non-empty `agent.run_id`, `agent.name`, and `agent.status` on the agent root. Evidence includes the root span ID, missing attributes, present attribute names, and expected attributes.

### TG-TEL-003A

`TOOL_PARENT_CHAIN`, blocking, trace-level. Every tool span must have a resolvable parent chain ending at the single `agent.run` root. Evidence includes tool span IDs, visited parents, missing parents or cycle paths, expected root ID, and termination reason.

### TG-TEL-003B

`NO_TRACE_FRAGMENTATION`, blocking, run-level. Trace-only inputs return `NOT_APPLICABLE` because run-level collection was not supplied. Run bundles fail when one `agent.run_id` appears across multiple trace IDs.

### TG-TEL-004

`TOOL_STATUS`, blocking, trace-level. Every tool span must contain non-empty `tool.status`. No tool spans returns `NOT_APPLICABLE`.

### TG-TEL-005

`MODEL_IDENTITY`, blocking, trace-level. Every model span must contain non-empty `gen_ai.request.model`. No model spans returns `NOT_APPLICABLE`.

### TG-TEL-006

`TOKEN_USAGE`, warning, trace-level. Every model span must contain nonnegative real integer `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`; booleans are invalid. No model spans returns `NOT_APPLICABLE`.

### TG-TEL-007

`TIMESTAMP_VALIDITY`, blocking, trace-level. Every span must contain timezone-aware `start_time` and `end_time`, real integer nonnegative `duration_nano`, and `end_time` must not precede `start_time`. Exact duration equality is not required.

### TG-TEL-008

`LOG_CORRELATION`, warning, run-level. Trace-only inputs return `NOT_APPLICABLE` because correlated logs were not supplied. Run bundles with logs require matching `agent.run_id`, known trace IDs when trace correlation is expected, and known span IDs when supplied.

## Structural TG-STR Rules

`TG-STR-001 TRACE_HAS_SPANS`, blocking, requires at least one span.

`TG-STR-002 REQUIRED_SPAN_IDENTITY`, blocking, requires non-empty `trace_id`, `span_id`, and `span_name`.

`TG-STR-003 TRACE_ID_CONSISTENCY`, blocking, requires every span trace ID to match the enclosing trace ID.

`TG-STR-004 UNIQUE_SPAN_IDS`, blocking, requires unique non-empty span IDs inside one trace.

`TG-STR-005 SERVICE_IDENTITY`, warning, requires `service_name` or `resource_attributes["service.name"]`.
