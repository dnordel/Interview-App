from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
VALIDATION_INLINE_COLORS = {
    VALIDATION_SEVERITY_INFO: "#1d4ed8",
    VALIDATION_SEVERITY_WARNING: "#92400e",
    VALIDATION_SEVERITY_ERROR: "#b91c1c",
    VALIDATION_SEVERITY_BLOCKING: "#991b1b",
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


@dataclass(slots=True)
class InlineValidationMessage:
    message_var: Any
    message_label: Any

    def show(
        self,
        *,
        issue: str,
        next_step: str,
        focus_widget: Any | None = None,
        severity: str = VALIDATION_SEVERITY_ERROR,
    ) -> None:
        normalized = severity if severity in VALIDATION_SEVERITIES else VALIDATION_SEVERITY_ERROR
        if hasattr(self.message_label, "configure"):
            self.message_label.configure(foreground=VALIDATION_INLINE_COLORS[normalized])
        if hasattr(self.message_var, "set"):
            self.message_var.set(format_guidance(sanitize_user_error(issue), next_step))
        if focus_widget is not None and hasattr(focus_widget, "focus_set"):
            focus_widget.focus_set()

    def clear(self) -> None:
        if hasattr(self.message_var, "set"):
            self.message_var.set("")


def append_error_log(log_path: Path, title: str, technical_details: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{ts}] {title}\n{technical_details.rstrip()}\n")


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
