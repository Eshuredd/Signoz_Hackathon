# Gate 3

Gate 3 is the deterministic TraceGuard telemetry evaluator. The initial Gate 3A implementation was a useful prototype, but it used noncanonical `TG-TEL-*` rule assignments. Repository head now uses `traceguard-telemetry-v2`.

The canonical contract is in `gate3/spec/traceguard_telemetry_contract_v1.md`; the machine-readable catalogue is `gate3/spec/ruleset_v1.json`; migration from old prototype IDs is documented in `gate3/spec/legacy_rule_migration.json`.

Public verdicts are `PASS`, `PASS_WITH_WARNINGS`, and `BLOCK`. Each evaluation emits one `RuleResult` for every registered rule with status `PASSED`, `FAILED`, `NOT_APPLICABLE`, or `EVALUATION_ERROR`.

`TG-TEL-*` rules now cover canonical telemetry quality: agent root, agent attributes, tool parent chains, run fragmentation, tool status, model identity, token usage, timestamp validity, and log correlation. Structural checks moved to `TG-STR-*`.

Trace-only evaluation cannot prove run-level fragmentation or log correlation, so `TG-TEL-003B` and `TG-TEL-008` return `NOT_APPLICABLE`. Run-bundle evaluation aggregates trace-level rules across supplied traces and evaluates run-level rules directly.

Run bundles must contain at least one trace. `TG-TEL-003B` validates bundle membership using the bundle `agent_run_id`: at least one supplied `agent.run` root must match the bundle ID, matching traces must resolve to exactly one unique trace ID, and any supplied trace with a different non-empty `agent.run_id` is a foreign-run failure.

`traceguard.run_id`, `traceguard.project`, and `traceguard.gate` are not required evaluator input attributes. They may remain in metadata, preflight search attributes, runtime artifacts, or evaluator-output telemetry.

CLI:

```powershell
python gate3/cli.py evaluate-trace gate3/fixtures/trace/pass_canonical_agent_trace.json
python gate3/cli.py evaluate-run gate3/fixtures/run/pass_single_trace_run.json
python gate3/cli.py evaluate-all
python gate3/cli.py validate-fixtures
python gate3/cli.py list-rules
```

Individual exit codes are `0` for `PASS`, `10` for `PASS_WITH_WARNINGS`, `20` for `BLOCK`, `2` for invalid input, and `3` for internal evaluator failure.

Gate 3 remains network-independent. Live SigNoz proof lives in `gate3_preflight/`.

Live contract completion remains dependent on a successful `gate3_preflight` run against the local SigNoz and OTLP collector stack.
