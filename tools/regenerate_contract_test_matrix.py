from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "contracts"
MATRIX_PATH = ROOT / "docs" / "contract_test_coverage_matrix.yaml"


@dataclass(frozen=True)
class Rule:
    predicate: Any
    tests: list[dict[str, Any]]


def _is_module_contract(payload: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and "module" in payload


def _relative_contract_path(contract_path: Path) -> str:
    resolved_path = contract_path.resolve()
    return str(resolved_path.relative_to(ROOT)).replace("\\", "/")


def _function_symbol_entries(
    contract_name: str,
    module_name: str,
    functions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for fn in functions:
        name = fn.get("name")
        if not name:
            continue
        entries.append(
            {
                "contract": contract_name,
                "symbol": f"{module_name}.{name}",
                "kind": "function",
            }
        )
    return entries


def _class_method_symbol_entries(
    contract_name: str,
    module_name: str,
    classes: list[dict[str, Any]],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for cls in classes:
        class_name = cls.get("name")
        if not class_name:
            continue
        for method in cls.get("methods", []):
            method_name = method.get("name")
            if not method_name:
                continue
            entries.append(
                {
                    "contract": contract_name,
                    "symbol": f"{module_name}.{class_name}.{method_name}",
                    "kind": "method",
                }
            )
    return entries


def _symbol_entries(contract_path: Path, payload: dict[str, Any]) -> list[dict[str, str]]:
    module_name = payload["module"]["name"]
    contract_name = _relative_contract_path(contract_path)
    entries = [
        *_function_symbol_entries(contract_name, module_name, payload.get("functions", [])),
        *_class_method_symbol_entries(contract_name, module_name, payload.get("classes", [])),
    ]
    return sorted(entries, key=lambda entry: entry["symbol"])


def _mapping_rules() -> list[Rule]:
    return [
        Rule(
            predicate=lambda symbol, contract: contract.startswith("contracts/onboarding_"),
            tests=[
                {
                    "path": "tests/test_onboarding_contract_interfaces.py",
                    "focus": ["behavior", "parameter_validation", "return_type", "security_validation"],
                }
            ],
        ),
        Rule(
            predicate=lambda symbol, contract: contract.startswith("contracts/interview_app_"),
            tests=[
                {
                    "path": "tests/test_interview_app_contract_interfaces.py",
                    "focus": ["behavior", "parameter_validation", "return_type", "security_side_effects"],
                }
            ],
        ),
        Rule(
            predicate=lambda symbol, contract: contract
            in {
                "contracts/runtime_wrapper.contract.yaml",
                "contracts/interview_runtime.contract.yaml",
            },
            tests=[
                {
                    "path": "tests/test_interview_root_contracts.py",
                    "focus": ["behavior", "parameter_validation", "return_type", "security_leakage_prevention"],
                }
            ],
        ),
        Rule(
            predicate=lambda symbol, contract: contract
            in {
                "contracts/app_logging.contract.yaml",
                "contracts/scoring_reporting.contract.yaml",
                "contracts/scoring_reporting.contract.yaml",
            },
            tests=[
                {
                    "path": "tests/test_shared_module_contract_interfaces.py",
                    "focus": ["behavior", "parameter_validation", "return_type", "security_controls"],
                }
            ],
        ),
    ]


def _tests_for_symbol(symbol: str, contract: str) -> list[dict[str, Any]]:
    for rule in _mapping_rules():
        if rule.predicate(symbol, contract):
            return rule.tests
    return [
        {
            "path": "tests/test_contract_coverage_matrix.py",
            "focus": ["behavior", "parameter_validation", "return_type"],
        }
    ]


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for entry in entries:
        symbol = entry["symbol"]
        if symbol in seen:
            raise ValueError(f"Duplicate contract symbol: {symbol}")
        seen.add(symbol)
        deduped.append(entry)
    return deduped


def build_matrix() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []

    for contract_path in sorted(CONTRACTS_DIR.glob("*.contract.yaml")):
        payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        if not _is_module_contract(payload):
            continue

        for symbol_entry in _symbol_entries(contract_path, payload):
            tests = _tests_for_symbol(symbol_entry["symbol"], symbol_entry["contract"])
            entries.append({**symbol_entry, "tests": tests})

    entries = _dedupe_entries(sorted(entries, key=lambda entry: entry["symbol"]))

    return {
        "last_updated": date.today().isoformat(),
        "sections": [
            {"name": "interview_app", "tests": ["tests/test_interview_app_contract_interfaces.py"]},
            {"name": "onboarding", "tests": ["tests/test_onboarding_contract_interfaces.py"]},
            {"name": "shared", "tests": ["tests/test_shared_module_contract_interfaces.py"]},
            {"name": "root", "tests": ["tests/test_interview_root_contracts.py"]},
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "regenerate_command": "python tools/regenerate_contract_test_matrix.py",
        "security_test_mapping": {
            "validation": [
                "tests/test_onboarding_contract_interfaces.py",
                "tests/test_shared_module_contract_interfaces.py",
            ],
            "auth_expectations": [
                "tests/test_interview_root_contracts.py",
                "tests/test_onboarding_contract_interfaces.py",
            ],
            "side_effect_controls": [
                "tests/test_interview_app_contract_interfaces.py",
                "tests/test_shared_module_contract_interfaces.py",
            ],
            "leakage_prevention": [
                "tests/test_interview_root_contracts.py",
                "tests/test_shared_module_contract_interfaces.py",
            ],
        },
        "entries": entries,
    }


def main() -> int:
    matrix = build_matrix()
    MATRIX_PATH.write_text(yaml.safe_dump(matrix, sort_keys=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
