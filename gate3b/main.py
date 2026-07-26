from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate3b.runner import run_gate3b
from gate3b.scenarios import get_definition, scenario_catalogue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TraceGuard Gate 3B live run-level validation.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list-scenarios", action="store_true", help="print the static scenario catalogue")
    mode.add_argument("--check-environment", action="store_true", help="validate SigNoz/OTLP configuration without emitting telemetry")
    mode.add_argument("--scenario", help="run one exact scenario name")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print(json.dumps(scenario_catalogue(), indent=2, sort_keys=True))
        return 0
    if args.scenario:
        try:
            get_definition(args.scenario)
        except KeyError:
            parser.error(f"unknown Gate 3B scenario: {args.scenario}")
    return run_gate3b(selected_scenario_name=args.scenario, check_environment_only=args.check_environment)


if __name__ == "__main__":
    raise SystemExit(main())

