from __future__ import annotations

import sys
from pathlib import Path

from comparison import compare_sources, render_report, write_report
from config import Gate2Config
from exceptions import Gate2Error
from logging_config import configure_logging
from mcp_probe import run_mcp_probe
from signoz_api_client import run_trace_api_probe


def main() -> int:
    try:
        config = Gate2Config.from_env()
        logger = configure_logging(config.debug)
        artifacts_dir = Path(__file__).resolve().parent / "artifacts"

        trace_api = run_trace_api_probe(config, logger, artifacts_dir)
        mcp = run_mcp_probe(config, logger, artifacts_dir)
        report = compare_sources(trace_api, mcp)
        report_path = write_report(artifacts_dir / "gate2_comparison.json", report)

        print(render_report(report))
        print(f"JSON artifact: {report_path}")

        return 0 if trace_api.trace is not None else 1
    except Gate2Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
