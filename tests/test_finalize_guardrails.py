from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

from interview_app.finalize_pipeline import (
    LEGACY_FINALIZE_GUARDRAIL_MESSAGE,
    raise_legacy_finalize_guardrail,
)
from reporting import ReportingValidationError


def _load_interview_app_module():
    module_path = Path(__file__).resolve().parents[1] / "src" / "interview_app.pyw"
    loader = SourceFileLoader("interview_app_guardrail_test", str(module_path))
    spec = importlib.util.spec_from_loader("interview_app_guardrail_test", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raise_legacy_finalize_guardrail_raises_reporting_validation_error() -> None:
    with pytest.raises(ReportingValidationError, match="Legacy finalize scoring is disabled"):
        raise_legacy_finalize_guardrail()


def test_run_finalize_pipeline_initializes_controller_instead_of_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    interview_app = _load_interview_app_module()
    app = SimpleNamespace()
    app.shared_state = SimpleNamespace()

    run_calls = {"count": 0}

    class _ControllerStub:
        def run_finalize_pipeline(self):
            run_calls["count"] += 1
            return {"ok": True}

    monkeypatch.setattr(interview_app, "FinalizePipelineController", lambda _app, _shared_state: _ControllerStub())

    result = interview_app.InterviewApp._run_finalize_pipeline(app)

    assert result == {"ok": True}
    assert run_calls["count"] == 1
    assert isinstance(app.finalize_pipeline_controller, _ControllerStub)


def test_legacy_finalize_entrypoint_is_blocked() -> None:
    interview_app = _load_interview_app_module()
    app = SimpleNamespace()

    with pytest.raises(ReportingValidationError, match=LEGACY_FINALIZE_GUARDRAIL_MESSAGE):
        interview_app.InterviewApp._run_finalize_pipeline_legacy(app)
