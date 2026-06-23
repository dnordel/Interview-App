from __future__ import annotations

import importlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from app_logging import RedactionFilter
from scoring_reporting import sanitize_email_subject, sender_email_error_reason, serialize_integration_payload
from storage_utils import atomic_write_json, safe_read_json
from interview_runtime import probe_audio_file, write_transcription_diagnostic

SHARED_CONTRACTS = [
    Path("contracts/storage_utils.contract.yaml"),
    Path("contracts/app_logging.contract.yaml"),
    Path("contracts/scoring_reporting.contract.yaml"),
    Path("contracts/interview_runtime.contract.yaml"),
]


def _load_module(path: str):
    module_name = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return importlib.import_module(module_name)


def _iter_shared_cases() -> list[tuple[str, str, str, dict[str, Any], str | None]]:
    cases: list[tuple[str, str, str, dict[str, Any], str | None]] = []
    for contract_path in SHARED_CONTRACTS:
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


@pytest.mark.parametrize(
    "contract_path,module_path,symbol_name,contract_inputs,class_name",
    _iter_shared_cases(),
)
def test_shared_contract_symbol_signature_exists(
    contract_path: str,
    module_path: str,
    symbol_name: str,
    contract_inputs: dict[str, Any],
    class_name: str | None,
) -> None:
    module = _load_module(module_path)
    target = getattr(getattr(module, class_name), symbol_name) if class_name else getattr(module, symbol_name)
    expected_inputs = list(contract_inputs.keys())
    if isinstance(target, property):
        assert expected_inputs == [], contract_path
        return
    assert _param_names(inspect.signature(target), method=bool(class_name)) == expected_inputs, contract_path


def test_shared_contract_entries_declare_return_types() -> None:
    for contract_path in SHARED_CONTRACTS:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        for fn in data.get("functions", []):
            assert isinstance(fn.get("returns", {}).get("type"), str) and fn["returns"]["type"]
        for cls in data.get("classes", []):
            for method in cls.get("methods", []):
                assert isinstance(method.get("returns", {}).get("type"), str) and method["returns"]["type"]


def test_security_logging_redacts_sensitive_fields_and_text() -> None:
    record = logging.makeLogRecord(
        {
            "name": "app",
            "level": logging.INFO,
            "msg": "Reach me at teacher@example.com and +1 (555) 123-4567",
            "args": (),
            "email": "teacher@example.com",
            "candidate_name": "Jordan",
        }
    )

    allowed = RedactionFilter().filter(record)

    assert allowed is True
    assert record.email == "[REDACTED]"
    assert record.candidate_name == "[REDACTED]"
    assert "teacher@example.com" not in record.msg
    assert "555" not in record.msg


def test_security_filesystem_boundaries_and_diagnostics_artifacts(tmp_path: Path) -> None:
    data_path = tmp_path / "state" / "payload.json"
    atomic_write_json(data_path, {"safe": True})
    assert safe_read_json(data_path, default={}) == {"safe": True}

    diag_path = write_transcription_diagnostic(
        output_dir=tmp_path,
        base_name="candidate",
        stage="recording error",
        error=RuntimeError("mic unavailable"),
        context={"device": "default"},
    )
    payload = json.loads(diag_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "recording_error"
    assert payload["error_type"] == "RuntimeError"
    assert probe_audio_file(tmp_path / "missing.wav")["exists"] is False


def test_atomic_write_json_retries_transient_windows_replace_denial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_path = tmp_path / "question_overrides.json"
    data_path.write_text('{"old": true}', encoding="utf-8")
    original_replace = Path.replace
    calls = {"count": 0}

    def flaky_replace(self: Path, target: Path) -> Path:
        if Path(target) == data_path and calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError(5, "Access is denied", str(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    atomic_write_json(data_path, {"new": True})

    assert calls["count"] == 1
    assert safe_read_json(data_path, default={}) == {"new": True}


def test_security_email_and_export_controls(tmp_path: Path) -> None:
    assert sanitize_email_subject(" Hello\r\nWorld ") == "Hello  World"
    assert sender_email_error_reason("bad-address") == "invalid_format"

    export_path = serialize_integration_payload(
        tmp_path,
        {"candidate": {"name": "A"}},
        candidate_name="A",
    )
    assert export_path.exists()

    assert export_path.parent.name == "integration_exports"
    assert ".." not in export_path.name
