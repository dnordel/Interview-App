from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from interview_app.history_actions import HistoryActionsService
from interview_runtime import AudioRuntimeController, TranscriptionQueueState, _value_or_default


_CONTRACT_ROOT = Path("contracts")


def _load_module(path: str):
    module_name = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return importlib.import_module(module_name)


def _iter_contract_cases() -> list[tuple[str, str, str, dict[str, Any], str | None]]:
    cases: list[tuple[str, str, str, dict[str, Any], str | None]] = []
    for contract_path in sorted(_CONTRACT_ROOT.glob("interview_app_*.contract.yaml")):
        data = yaml.safe_load(contract_path.read_text())
        module_path = data["module"]["path"]
        for fn in data.get("functions", []):
            cases.append((str(contract_path), module_path, fn["name"], fn.get("inputs", {}), None))
        for cls in data.get("classes", []):
            for method in cls.get("methods", []):
                cases.append((str(contract_path), module_path, method["name"], method.get("inputs", {}), cls["name"]))
    return cases


def _param_names(sig: inspect.Signature, *, method: bool) -> list[str]:
    params = []
    for idx, param in enumerate(sig.parameters.values()):
        if method and idx == 0 and param.name == "self":
            continue
        name = param.name
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            name = f"*{name}"
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            name = f"**{name}"
        params.append(name)
    return params


@pytest.mark.parametrize(
    "contract_path,module_path,symbol_name,contract_inputs,class_name",
    _iter_contract_cases(),
)
def test_contract_symbol_signature_exists(
    contract_path: str,
    module_path: str,
    symbol_name: str,
    contract_inputs: dict[str, Any],
    class_name: str | None,
) -> None:
    module = _load_module(module_path)
    target = getattr(getattr(module, class_name), symbol_name) if class_name else getattr(module, symbol_name)
    signature = inspect.signature(target)
    assert _param_names(signature, method=bool(class_name)) == list(contract_inputs.keys()), contract_path


def test_security_value_or_default_enforces_stripped_defaults() -> None:
    assert _value_or_default({}, runtime_key="r", preferred_key="p", default_value="safe") == "safe"
    assert _value_or_default({"r": "  gpu  "}, runtime_key="r", preferred_key="p", default_value="safe") == "gpu"


def test_security_transcription_cancel_is_side_effect_safe() -> None:
    app = SimpleNamespace()
    import threading

    app._audio_state_lock = threading.Lock()
    queue_state = TranscriptionQueueState()
    app._transcription_queue_state = queue_state
    app._append_recording_attempt = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not append"))
    app._persist_interview_session_snapshot = lambda _idx: None
    app._delete_file_if_exists = lambda _path: None
    app.state = SimpleNamespace(flow_candidate_transcripts={})
    queue_state.enqueue(
        7,
        {
            "flow_idx": 7,
            "session": object(),
            "base_dir": Path("/tmp"),
            "base_name": "b",
            "candidate_label": "candidate",
        },
    )
    queue_state.cancel(7)

    controller = AudioRuntimeController(app, SimpleNamespace())
    controller.background_transcribe_question(
        flow_idx=7,
        session=SimpleNamespace(stop_and_transcribe=lambda **_kwargs: None),
        base_dir=Path("/tmp"),
        base_name="b",
        candidate_label="candidate",
    )

    assert app.state.flow_candidate_transcripts == {}


def test_security_offer_transition_requires_known_status() -> None:
    service = HistoryActionsService(SimpleNamespace())
    assert service.offer_transition("INVALID") is None


def test_security_queue_cancel_is_idempotent() -> None:
    queue_state = TranscriptionQueueState()
    queue_state.enqueue(3, {"flow_idx": 3, "session": object(), "base_dir": Path("/tmp"), "base_name": "x", "candidate_label": "c"})
    first = queue_state.cancel(3)
    second = queue_state.cancel(3)

    assert first["is_canceled"] is True
    assert second["is_canceled"] is True
