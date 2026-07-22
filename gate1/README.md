# TraceGuard Gate 1A

Minimal OpenTelemetry proof that a local Python process can export telemetry to a locally running SigNoz instance.

This intentionally sends only:

- one custom trace from service `traceguard-gate1`
- one custom counter metric increment
- one optional structured log, best effort only

## Setup

From the repository root:

```bash
cd gate1
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

With SigNoz already running locally:

```bash
python telemetry.py
```

By default, the script exports over OTLP/HTTP to:

- traces: `http://localhost:4318/v1/traces`
- metrics: `http://localhost:4318/v1/metrics`
- logs: `http://localhost:4318/v1/logs`

Override the base endpoint if needed:

```bash
TRACEGUARD_OTLP_ENDPOINT=http://localhost:4318 python telemetry.py
```

Or override each signal endpoint:

```bash
TRACEGUARD_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces \
TRACEGUARD_OTLP_METRICS_ENDPOINT=http://localhost:4318/v1/metrics \
TRACEGUARD_OTLP_LOGS_ENDPOINT=http://localhost:4318/v1/logs \
python telemetry.py
```

The standard OpenTelemetry variables also work:

- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
- `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`
- `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`
- `OTEL_EXPORTER_OTLP_TIMEOUT`

## Verify In SigNoz

Open the local SigNoz UI, usually:

```text
http://localhost:8080
```

Trace:

1. Go to **Traces**.
2. Filter by service name `traceguard-gate1`.
3. Look for span name `traceguard.gate1.connectivity`.
4. Open the trace and confirm attributes such as `traceguard.project`, `traceguard.gate`, and `traceguard.run_id`.

Metric:

1. Go to **Metrics** or the metrics explorer.
2. Search for `traceguard.gate1.connectivity_runs`.
3. Filter/group by service `traceguard-gate1` if needed.
4. Confirm the counter has exactly one increment from the latest script run.

If the script cannot export the required trace or metric, it exits non-zero and prints an `ERROR:` message with the failing endpoint.
