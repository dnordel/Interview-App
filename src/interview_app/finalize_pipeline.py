from __future__ import annotations

from types import SimpleNamespace


messagebox = SimpleNamespace(
    showinfo=lambda *_args, **_kwargs: None,
    showerror=lambda *_args, **_kwargs: None,
    askretrycancel=lambda *_args, **_kwargs: False,
)

from scoring_reporting import ReportingValidationError, ScoringEngine
from interview_runtime import (
    LEGACY_FINALIZE_GUARDRAIL_MESSAGE,
    PENDING_TRANSCRIPTION_WARNING,
    FinalizePipelineController,
    raise_legacy_finalize_guardrail,
    validate_before_finalize,
)

__all__ = [
    "LEGACY_FINALIZE_GUARDRAIL_MESSAGE",
    "PENDING_TRANSCRIPTION_WARNING",
    "FinalizePipelineController",
    "ReportingValidationError",
    "ScoringEngine",
    "messagebox",
    "raise_legacy_finalize_guardrail",
    "validate_before_finalize",
]
