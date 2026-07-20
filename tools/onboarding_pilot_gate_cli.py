from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from onboarding_pilot_gate import (  # noqa: E402
    REQUIRED_PILOT_SCENARIOS,
    approve_rollout,
    enabled_director_schools,
    evaluate_pilot_gate,
    record_pilot_day,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record and evaluate Onboarding pilot evidence.")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record-day")
    record.add_argument("--path", type=Path, required=True)
    record.add_argument("--date", type=date.fromisoformat, required=True)
    record.add_argument("--device", required=True)
    record.add_argument("--scenario", action="append", default=[])
    record.add_argument("--all-scenarios", action="store_true")
    record.add_argument("--defect", action="append", default=[])
    status = commands.add_parser("status")
    status.add_argument("--path", type=Path, required=True)
    approve = commands.add_parser("approve")
    approve.add_argument("--path", type=Path, required=True)
    approve.add_argument("--school", choices=["Hawthorne", "North Long Beach"], required=True)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--confirm-no-critical-high", action="store_true")
    return parser


def _defects(values: list[str]) -> tuple[dict[str, str], ...]:
    parsed: list[dict[str, str]] = []
    for value in values:
        severity, separator, remainder = value.partition(":")
        state, second_separator, category = remainder.partition(":")
        if not separator or not second_separator:
            raise ValueError("Defect must use severity:state:category format.")
        parsed.append({"severity": severity, "state": state, "category": category})
    return tuple(parsed)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "record-day":
        scenarios = REQUIRED_PILOT_SCENARIOS if args.all_scenarios else tuple(args.scenario)
        record_pilot_day(
            args.path, business_date=args.date, device_id=args.device,
            scenarios=scenarios, defects=_defects(args.defect),
        )
        return 0
    if args.command == "approve":
        approve_rollout(
            args.path, school=args.school, actor=args.actor,
            confirm_no_critical_high=args.confirm_no_critical_high, reason=args.reason,
        )
        return 0
    result = evaluate_pilot_gate(args.path)
    payload = asdict(result)
    payload["enabled_director_schools"] = enabled_director_schools(args.path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
