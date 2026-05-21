from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CheckResult:
    name: str
    ok: bool
    messages: list[str]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _check_baseline_section(path: Path) -> CheckResult:
    data = _load_yaml(path)
    messages: list[str] = []
    required = ["baseline_version", "generated_artifacts", "module_inventory"]

    for key in required:
        if key not in data:
            messages.append(f"Missing key '{key}' in {path}")

    inventory = data.get("module_inventory")
    if not isinstance(inventory, list) or not inventory:
        messages.append("module_inventory must be a non-empty list")

    return CheckResult("baseline", not messages, messages)


def _check_locked_section(path: Path) -> CheckResult:
    data = _load_yaml(path)
    messages: list[str] = []

    if "violations" not in data:
        messages.append(f"Missing key 'violations' in {path}")

    violations = data.get("violations")
    if violations is not None and not isinstance(violations, list):
        messages.append("violations must be a list")

    return CheckResult("locked", not messages, messages)


def _check_contract_schema(contract_dir: Path) -> CheckResult:
    messages: list[str] = []
    required_module_keys = ["name", "language", "path", "version", "description"]

    for contract_path in sorted(contract_dir.glob("*.contract.yaml")):
        if contract_path.name in {"system.contract.yaml", "architecture.contract.yaml"}:
            continue
        data = _load_yaml(contract_path)
        module = data.get("module", {})

        for key in required_module_keys:
            if key not in module:
                messages.append(f"{contract_path}: module.{key} is required")

        for section in ["functions", "classes", "data_structures", "dependencies"]:
            if section not in data:
                messages.append(f"{contract_path}: missing top-level '{section}'")

    return CheckResult("schema", not messages, messages)


def _append_section_messages(path: Path, item: Any, messages: list[str]) -> None:
    if not isinstance(item, dict):
        messages.append(f"{path}: each section entry must be a mapping")
        return

    name = item.get("name", "<unknown>")
    if not item.get("name"):
        messages.append(f"{path}: section missing name")

    tests = item.get("tests")
    if not isinstance(tests, list) or not tests:
        messages.append(f"{path}: section '{name}' missing tests")
        return

    missing_paths = [test_path for test_path in tests if not Path(test_path).exists()]
    for missing in missing_paths:
        messages.append(f"{path}: referenced test file not found: {missing}")


def _check_coverage_matrix(path: Path, max_age_days: int) -> CheckResult:
    data = _load_yaml(path)
    messages: list[str] = []
    reviewed = data.get("last_updated")

    if isinstance(reviewed, date):
        reviewed_date = reviewed
    elif isinstance(reviewed, str):
        try:
            reviewed_date = date.fromisoformat(reviewed)
        except ValueError:
            messages.append(f"{path}: invalid last_updated date format")
            reviewed_date = None
    else:
        messages.append(f"{path}: last_updated must be an ISO date string")
        reviewed_date = None

    if reviewed_date is not None:
        age_days = (date.today() - reviewed_date).days
        if age_days > max_age_days:
            messages.append(f"{path}: last_updated is stale by {age_days - max_age_days} day(s)")

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        messages.append(f"{path}: sections must be a non-empty list")
        return CheckResult("coverage-matrix", not messages, messages)

    for item in sections:
        _append_section_messages(path, item, messages)

    return CheckResult("coverage-matrix", not messages, messages)


def run_checks(sections: list[str], max_age_days: int) -> list[CheckResult]:
    results: list[CheckResult] = []

    if "baseline" in sections:
        results.append(_check_baseline_section(Path("docs/contract_baseline_checklist.yaml")))
    if "locked" in sections:
        results.append(_check_locked_section(Path("docs/contract_locked_validation.yaml")))
    if "schema" in sections:
        results.append(_check_contract_schema(Path("contracts")))
    if "coverage-matrix" in sections:
        results.append(_check_coverage_matrix(Path("docs/contract_test_coverage_matrix.yaml"), max_age_days))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight contract-review drift checks.")
    parser.add_argument(
        "--section",
        action="append",
        choices=["baseline", "locked", "schema", "coverage-matrix", "all"],
        help="Review section to execute (default: all).",
    )
    parser.add_argument("--max-age-days", type=int, default=45, help="Max age for coverage matrix freshness check.")
    args = parser.parse_args()

    selected = args.section or ["all"]
    sections = ["baseline", "locked", "schema", "coverage-matrix"] if "all" in selected else selected

    failed = False
    for result in run_checks(sections, max_age_days=args.max_age_days):
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}")
        for message in result.messages:
            print(f"  - {message}")
        failed = failed or not result.ok

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
