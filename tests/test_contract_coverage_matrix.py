from __future__ import annotations

from pathlib import Path

import yaml

MATRIX_PATH = Path("docs/contract_test_coverage_matrix.yaml")
CONTRACTS_DIR = Path("contracts")


def _contract_symbols() -> set[str]:
    symbols: set[str] = set()
    for contract_path in sorted(CONTRACTS_DIR.glob("*.contract.yaml")):
        payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        if "module" not in payload:
            continue
        module_name = payload["module"]["name"]
        for fn in payload.get("functions", []):
            symbols.add(f"{module_name}.{fn['name']}")
        for cls in payload.get("classes", []):
            for method in cls.get("methods", []):
                symbols.add(f"{module_name}.{cls['name']}.{method['name']}")
    return symbols


def test_matrix_covers_all_contract_symbols_1_to_1() -> None:
    matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    entries = matrix["entries"]
    matrix_symbols = {entry["symbol"] for entry in entries}
    assert _contract_symbols() == matrix_symbols

    for entry in entries:
        assert entry["tests"], entry["symbol"]
        first_test = entry["tests"][0]
        assert Path(first_test["path"]).exists(), entry["symbol"]


def test_matrix_has_security_mappings_for_review_cadence() -> None:
    matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    security_mapping = matrix["security_test_mapping"]
    required_keys = {"validation", "auth_expectations", "side_effect_controls", "leakage_prevention"}

    assert set(security_mapping.keys()) == required_keys
    for key in required_keys:
        for test_path in security_mapping[key]:
            assert Path(test_path).exists(), f"{key}:{test_path}"


def test_locked_guardrail_and_drift_detection_artifacts_present() -> None:
    locked = yaml.safe_load(Path("docs/contract_locked_validation.yaml").read_text(encoding="utf-8"))
    checklist = yaml.safe_load(Path("docs/contract_baseline_checklist.yaml").read_text(encoding="utf-8"))

    assert locked["violations"] == []
    assert "locked_interface_validation" in checklist
    assert "repeatable_checklist" in checklist
