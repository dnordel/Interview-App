from __future__ import annotations

from typing import Any

VALIDATION_SEVERITY_INFO = "info"
VALIDATION_SEVERITY_WARNING = "warning"
VALIDATION_SEVERITY_ERROR = "error"
VALIDATION_SEVERITY_BLOCKING = "blocking"
VALIDATION_SEVERITIES = {
    VALIDATION_SEVERITY_INFO,
    VALIDATION_SEVERITY_WARNING,
    VALIDATION_SEVERITY_ERROR,
    VALIDATION_SEVERITY_BLOCKING,
}
TRANSCRIPTION_PARTIAL_WARNING_COPY = "Transcription still processing in background; report may be partial."


def should_display_modal(*, severity: str, irreversible_action: bool = False) -> bool:
    return severity == VALIDATION_SEVERITY_BLOCKING or irreversible_action


def sanitize_user_error(message: str) -> str:
    clean = " ".join(str(message).replace("\n", " ").split())
    forbidden_fragments = ["traceback", "file \"", "line ", "exception", "error:"]
    lowered = clean.lower()
    for fragment in forbidden_fragments:
        if fragment in lowered:
            return "An unexpected system issue occurred."
    return clean


def format_guidance(issue: str, next_step: str) -> str:
    return f"{issue.strip()} {next_step.strip()}".strip()


def present_transcription_partial_warning(presenter: Any | None, *, auto_expire_ms: int | None = 12000) -> str:
    if presenter is not None and hasattr(presenter, "show"):
        presenter.show(TRANSCRIPTION_PARTIAL_WARNING_COPY, auto_expire_ms=auto_expire_ms)
    return TRANSCRIPTION_PARTIAL_WARNING_COPY


def show_inline_field_error(
    inline_validation: Any,
    *,
    field_label: str,
    cause: str,
    corrective_action: str,
    focus_widget: Any | None = None,
    severity: str = VALIDATION_SEVERITY_ERROR,
) -> None:
    issue = f"{field_label}: {sanitize_user_error(cause)}" if field_label else sanitize_user_error(cause)
    inline_validation.show(
        issue=issue,
        next_step=corrective_action,
        focus_widget=focus_widget,
        severity=severity,
    )
