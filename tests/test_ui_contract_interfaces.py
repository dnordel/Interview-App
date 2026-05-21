from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest
import yaml

from ui_feedback import sanitize_user_error, should_display_modal

CONTRACTS = [
    Path("contracts/question_screens.contract.yaml"),
    Path("contracts/ui_feedback.contract.yaml"),
    Path("contracts/ui_windows.contract.yaml"),
]


def _load_module(path: str):
    module_name = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return importlib.import_module(module_name)


def _iter_cases() -> list[tuple[str, str, str, dict[str, Any], str | None]]:
    cases: list[tuple[str, str, str, dict[str, Any], str | None]] = []
    for contract_path in CONTRACTS:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        module_path = data["module"]["path"]
        for fn in data.get("functions", []):
            cases.append((str(contract_path), module_path, fn["name"], fn.get("inputs", {}), None))
        for cls in data.get("classes", []):
            for method in cls.get("methods", []):
                cases.append((str(contract_path), module_path, method["name"], method.get("inputs", {}), cls["name"]))
    return cases


def _param_names(sig: inspect.Signature, *, method: bool) -> list[str]:
    params: list[str] = []
    for idx, param in enumerate(sig.parameters.values()):
        if method and idx == 0 and param.name == "self":
            continue
        params.append(param.name)
    return params


@pytest.mark.parametrize("contract_path,module_path,symbol_name,contract_inputs,class_name", _iter_cases())
def test_ui_contract_symbol_signatures(
    contract_path: str,
    module_path: str,
    symbol_name: str,
    contract_inputs: dict[str, Any],
    class_name: str | None,
) -> None:
    module = _load_module(module_path)
    target = getattr(getattr(module, class_name), symbol_name) if class_name else getattr(module, symbol_name)
    assert _param_names(inspect.signature(target), method=bool(class_name)) == list(contract_inputs.keys()), contract_path


def test_ui_contract_return_types_are_documented() -> None:
    for contract_path in CONTRACTS:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        for fn in data.get("functions", []):
            returns = fn.get("returns", {}).get("type")
            assert isinstance(returns, str) and returns
        for cls in data.get("classes", []):
            for method in cls.get("methods", []):
                returns = method.get("returns", {}).get("type")
                assert isinstance(returns, str) and returns


def test_security_ui_validation_controls_and_return_types() -> None:
    assert should_display_modal(severity="blocking") is True
    sanitized = sanitize_user_error("Traceback: File \"x.py\" line 1")
    assert sanitized == "An unexpected system issue occurred."
    assert isinstance(sanitized, str)
