from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import runtime_wrapper
from interview_state import InterviewState
from transcript_accumulator import append_candidate_segment_text


CONTRACTS = [
    Path("contracts/runtime_wrapper.contract.yaml"),
    Path("contracts/interview_state.contract.yaml"),
    Path("contracts/transcript_accumulator.contract.yaml"),
]


def _load_module(path: str):
    module_name = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return importlib.import_module(module_name)


def _contract_cases() -> list[tuple[str, str, str, dict[str, Any], str | None]]:
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
    names: list[str] = []
    for idx, param in enumerate(sig.parameters.values()):
        if method and idx == 0 and param.name == "self":
            continue
        name = param.name
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            name = f"*{name}"
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            name = f"**{name}"
        names.append(name)
    return names


@pytest.mark.parametrize("contract_path,module_path,symbol_name,contract_inputs,class_name", _contract_cases())
def test_interview_root_contract_signatures(
    contract_path: str,
    module_path: str,
    symbol_name: str,
    contract_inputs: dict[str, Any],
    class_name: str | None,
) -> None:
    module = _load_module(module_path)
    target = getattr(getattr(module, class_name), symbol_name) if class_name else getattr(module, symbol_name)
    assert _param_names(inspect.signature(target), method=bool(class_name)) == list(contract_inputs.keys()), contract_path


def test_security_parameter_validation_requires_runtime_target() -> None:
    with pytest.raises(SystemExit):
        runtime_wrapper.parse_args([])


def test_security_workflow_authorization_rejects_missing_target() -> None:
    with pytest.raises(FileNotFoundError):
        runtime_wrapper.main(["--target", "missing_script.py"])


def test_security_leakage_prevention_candidate_filtering() -> None:
    text = append_candidate_segment_text(
        "existing",
        [
            {"speaker": "INTERVIEWER", "text": "private prompt"},
            {"speaker": "CANDIDATE", "text": "safe answer"},
        ],
        candidate_label="CANDIDATE",
    )
    assert text == "existing safe answer"


def test_security_side_effect_boundary_transcript_accumulator_pure() -> None:
    segments = [SimpleNamespace(speaker="CANDIDATE", text="answer")]
    snapshot = list(segments)

    merged = append_candidate_segment_text("", segments, candidate_label="CANDIDATE")

    assert merged == "answer"
    assert segments == snapshot


def test_interview_state_to_dict_shape() -> None:
    state = InterviewState(candidate_name="A", track="Toddler")

    payload = state.to_dict()

    assert payload["candidate"]["name"] == "A"
    assert payload["candidate"]["track"] == "Toddler"
    assert isinstance(payload["flow_recordings"], dict)
