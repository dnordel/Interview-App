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


def _symbol_entries(contract_path: Path, payload: dict[str, Any]) -> list[dict[str, str]]:
    module_name = payload["module"]["name"]
    entries: list[dict[str, str]] = []

    for fn in payload.get("functions", []):
        entries.append(
            {
                "contract": str(contract_path.relative_to(ROOT)),
                "symbol": f"{module_name}.{fn['name']}",
                "kind": "function",
            }
        )

    for cls in payload.get("classes", []):
        for method in cls.get("methods", []):
            entries.append(
                {
                    "contract": str(contract_path.relative_to(ROOT)),
                    "symbol": f"{module_name}.{cls['name']}.{method['name']}",
                    "kind": "method",
                }
            )

    return entries


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
                "contracts/interview_state.contract.yaml",
                "contracts/transcript_accumulator.contract.yaml",
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
                "contracts/storage_utils.contract.yaml",
                "contracts/app_logging.contract.yaml",
                "contracts/reporting.contract.yaml",
                "contracts/template_placeholders.contract.yaml",
                "contracts/transcription_diagnostics.contract.yaml",
                "contracts/email_security.contract.yaml",
                "contracts/integration_export.contract.yaml",
            },
            tests=[
                {
                    "path": "tests/test_shared_module_contract_interfaces.py",
                    "focus": ["behavior", "parameter_validation", "return_type", "security_controls"],
                }
            ],
        ),
        Rule(
            predicate=lambda symbol, contract: contract
            in {
                "contracts/question_screens.contract.yaml",
                "contracts/ui_feedback.contract.yaml",
                "contracts/ui_windows.contract.yaml",
            },
            tests=[
                {
                    "path": "tests/test_ui_contract_interfaces.py",
                    "focus": ["behavior", "parameter_validation", "return_type", "security_validation"],
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


def build_matrix() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []

    for contract_path in sorted(CONTRACTS_DIR.glob("*.contract.yaml")):
        payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        if not _is_module_contract(payload):
            continue

        for symbol_entry in _symbol_entries(contract_path, payload):
            tests = _tests_for_symbol(symbol_entry["symbol"], symbol_entry["contract"])
            entries.append({**symbol_entry, "tests": tests})

    return {
        "last_updated": date.today().isoformat(),
        "sections": [
            {"name": "interview_app", "tests": ["tests/test_interview_app_contract_interfaces.py"]},
            {"name": "onboarding", "tests": ["tests/test_onboarding_contract_interfaces.py"]},
            {"name": "ui", "tests": ["tests/test_ui_contract_interfaces.py"]},
            {"name": "shared", "tests": ["tests/test_shared_module_contract_interfaces.py"]},
            {"name": "root", "tests": ["tests/test_interview_root_contracts.py"]},
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "regenerate_command": "python tools/regenerate_contract_test_matrix.py",
        "security_test_mapping": {
            "validation": [
                "tests/test_onboarding_contract_interfaces.py",
                "tests/test_ui_contract_interfaces.py",
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


def main() -> None:
    matrix = build_matrix()
    MATRIX_PATH.write_text(yaml.safe_dump(matrix, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main()
