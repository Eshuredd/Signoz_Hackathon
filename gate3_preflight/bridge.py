from __future__ import annotations

from gate2.models import Trace


def gate2_trace_to_gate3_envelope(trace: Trace) -> dict[str, object]:
    return {
        "schema_version": 1,
        "trace": {
            "trace_id": trace.trace_id,
            "spans": [span.to_dict() for span in trace.spans],
            "retrieved_at": trace.retrieved_at.isoformat(),
            "source": trace.source.value,
            "metadata": dict(trace.metadata),
        },
    }
