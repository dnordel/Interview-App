from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


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
    """Return True only for blocking failures or irreversible confirmations."""
    return severity == VALIDATION_SEVERITY_BLOCKING or irreversible_action


def sanitize_user_error(message: str) -> str:
    """Redact noisy technical details before rendering user-facing copy."""
    clean = " ".join(str(message).replace("\n", " ").split())
    forbidden_fragments = ["traceback", "file \"", "line ", "exception", "error:"]
    lowered = clean.lower()
    for fragment in forbidden_fragments:
        if fragment in lowered:
            return "An unexpected system issue occurred."
    return clean


def format_guidance(issue: str, next_step: str) -> str:
    """Return standardized copy: one sentence issue + one sentence next step."""
    return f"{issue.strip()} {next_step.strip()}".strip()


@dataclass(slots=True)
class InlineValidationMessage:
    """Reusable inline recoverable-validation presenter."""

    message_var: tk.StringVar
    message_label: ttk.Label

    def show(
        self,
        *,
        issue: str,
        next_step: str,
        focus_widget: tk.Widget | None = None,
        severity: str = VALIDATION_SEVERITY_ERROR,
    ) -> None:
        normalized_severity = severity if severity in VALIDATION_SEVERITIES else VALIDATION_SEVERITY_ERROR
        self.message_label.configure(foreground=VALIDATION_INLINE_COLORS[normalized_severity])
        self.message_var.set(format_guidance(sanitize_user_error(issue), next_step))
        if focus_widget is None:
            return
        focus_widget.focus_set()

    def clear(self) -> None:
        self.message_var.set("")


@dataclass(slots=True)
class MainGuiWarningPresenter:
    """Non-blocking dismissible warning presenter for main-window alerts."""

    parent: tk.Misc
    frame: ttk.Frame
    message_var: tk.StringVar
    message_label: ttk.Label
    dismiss_button: ttk.Button
    _after_id: str | None = None

    def _find_pack_before_widget(self) -> tk.Widget | None:
        for widget in self.parent.winfo_children():
            if widget is self.frame:
                continue
            if widget.winfo_manager() == "pack":
                return widget
        return None

    def show(self, message: str, *, auto_expire_ms: int | None = 12000) -> None:
        clean_message = sanitize_user_error(message)
        if not clean_message:
            self.dismiss()
            return
        self.message_var.set(clean_message)
        if not self.frame.winfo_ismapped():
            before_widget = self._find_pack_before_widget()
            pack_kwargs = {"fill": "x", "padx": 8, "pady": (0, 6)}
            self._pack_frame_with_optional_before(pack_kwargs=pack_kwargs, before_widget=before_widget)
        self._cancel_timer()
        if auto_expire_ms is None or auto_expire_ms <= 0:
            return
        self._after_id = self.parent.after(auto_expire_ms, self.dismiss)

    def _pack_frame_with_optional_before(
        self,
        *,
        pack_kwargs: dict[str, object],
        before_widget: tk.Widget | None,
    ) -> None:
        if before_widget is None:
            self.frame.pack(**pack_kwargs)
            return
        try:
            self.frame.pack(**pack_kwargs, before=before_widget)
        except tk.TclError:
            self.frame.pack(**pack_kwargs)

    def dismiss(self) -> None:
        self._cancel_timer()
        self.message_var.set("")
        if self.frame.winfo_ismapped():
            self.frame.pack_forget()

    def _cancel_timer(self) -> None:
        if self._after_id is None:
            return
        self.parent.after_cancel(self._after_id)
        self._after_id = None


def create_main_gui_warning_presenter(
    parent: tk.Misc,
    *,
    on_dismiss: Callable[[], None] | None = None,
) -> MainGuiWarningPresenter:
    frame = ttk.Frame(parent, padding=(10, 6))
    message_var = tk.StringVar(value="")
    message_label = ttk.Label(frame, textvariable=message_var, foreground="#92400e", justify="left", wraplength=900)
    message_label.pack(side="left", fill="x", expand=True)

    def _dismiss() -> None:
        presenter.dismiss()
        if on_dismiss is not None:
            on_dismiss()

    dismiss_button = ttk.Button(frame, text="Dismiss", command=_dismiss)
    dismiss_button.pack(side="right", padx=(10, 0))
    presenter = MainGuiWarningPresenter(
        parent=parent,
        frame=frame,
        message_var=message_var,
        message_label=message_label,
        dismiss_button=dismiss_button,
    )
    return presenter


def present_transcription_partial_warning(
    presenter: MainGuiWarningPresenter | None,
    *,
    auto_expire_ms: int | None = 12000,
) -> str:
    """Display standardized transcript-partial warning copy when a presenter is available."""
    if presenter is None:
        return TRANSCRIPTION_PARTIAL_WARNING_COPY
    presenter.show(TRANSCRIPTION_PARTIAL_WARNING_COPY, auto_expire_ms=auto_expire_ms)
    return TRANSCRIPTION_PARTIAL_WARNING_COPY


def create_inline_validation_message(parent: tk.Misc, *, pady: tuple[int, int] = (0, 8)) -> InlineValidationMessage:
    message_var = tk.StringVar(value="")
    message_label = ttk.Label(parent, textvariable=message_var, foreground="#b91c1c", wraplength=760, justify="left")
    message_label.pack(anchor="w", padx=10, pady=pady)
    return InlineValidationMessage(message_var=message_var, message_label=message_label)


def create_inline_validation_message_grid(
    parent: tk.Misc,
    *,
    row: int,
    column: int = 0,
    columnspan: int = 1,
    padx: int | tuple[int, int] = 8,
    pady: tuple[int, int] = (0, 8),
    sticky: str = "w",
) -> InlineValidationMessage:
    message_var = tk.StringVar(value="")
    message_label = ttk.Label(parent, textvariable=message_var, foreground="#b91c1c", wraplength=760, justify="left")
    message_label.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, padx=padx, pady=pady)
    return InlineValidationMessage(message_var=message_var, message_label=message_label)


def show_inline_field_error(
    inline_validation: InlineValidationMessage,
    *,
    field_label: str,
    cause: str,
    corrective_action: str,
    focus_widget: tk.Widget | None = None,
    severity: str = VALIDATION_SEVERITY_ERROR,
) -> None:
    issue = f"{field_label}: {sanitize_user_error(cause)}" if field_label else sanitize_user_error(cause)
    inline_validation.show(issue=issue, next_step=corrective_action, focus_widget=focus_widget, severity=severity)


def associate_label_with_control(label: ttk.Label | tk.Label, control: tk.Widget) -> None:
    """Provide keyboard and pointer affordances between label and input control."""
    control.configure(takefocus=True)
    label.configure(cursor="hand2")
    label.bind("<Button-1>", lambda _event: control.focus_set())


def append_error_log(log_path: Path, title: str, technical_details: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] {title}\n{technical_details.rstrip()}\n")


def show_actionable_error(
    parent: tk.Misc,
    *,
    title: str,
    issue: str,
    next_step: str,
    technical_details: str | None = None,
) -> None:
    """Show user-facing guidance and optionally allow copying technical details."""
    safe_issue = sanitize_user_error(issue)
    if not technical_details:
        messagebox.showerror(title, format_guidance(safe_issue, next_step), parent=parent)
        return

    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.grab_set()
    win.resizable(False, False)

    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text=format_guidance(safe_issue, next_step),
        wraplength=520,
        justify="left",
    ).pack(anchor="w", pady=(0, 10))

    button_row = ttk.Frame(frame)
    button_row.pack(fill="x")

    def _copy_technical_details() -> None:
        win.clipboard_clear()
        win.clipboard_append(technical_details)
        messagebox.showinfo(
            "Technical details copied",
            format_guidance(
                "Technical details were copied to your clipboard.",
                "Paste them into a support message if troubleshooting is needed.",
            ),
            parent=win,
        )

    ttk.Button(button_row, text="Copy technical details", command=_copy_technical_details).pack(side="left")
    ttk.Button(button_row, text="OK", command=win.destroy).pack(side="right")

    win.wait_window()
