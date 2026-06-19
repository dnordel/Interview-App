import tkinter as tk

from ui_composition import (
    MainGuiWarningPresenter,
    TRANSCRIPTION_PARTIAL_WARNING_COPY,
    VALIDATION_SEVERITY_WARNING,
    format_guidance,
    present_transcription_partial_warning,
    sanitize_user_error,
    should_display_modal,
    show_inline_field_error,
)


class FakeInlineValidation:
    def __init__(self):
        self.kwargs = None

    def show(self, **kwargs):
        self.kwargs = kwargs


def test_format_guidance_returns_short_cause_and_action():
    assert format_guidance("Issue.", "Do this next.") == "Issue. Do this next."


def test_show_inline_field_error_formats_message_and_focus():
    inline = FakeInlineValidation()
    focus = object()

    show_inline_field_error(
        inline,
        field_label="Start date",
        cause="the date format is invalid.",
        corrective_action="Use YYYY-MM-DD.",
        focus_widget=focus,
    )

    assert inline.kwargs == {
        "issue": "Start date: the date format is invalid.",
        "next_step": "Use YYYY-MM-DD.",
        "focus_widget": focus,
        "severity": "error",
    }


def test_show_inline_field_error_forwards_severity():
    inline = FakeInlineValidation()

    show_inline_field_error(
        inline,
        field_label="Recipients",
        cause="some addresses are invalid.",
        corrective_action="Fix addresses and retry.",
        severity=VALIDATION_SEVERITY_WARNING,
    )

    assert inline.kwargs["severity"] == VALIDATION_SEVERITY_WARNING


def test_should_display_modal_only_for_blocking_or_irreversible():
    assert should_display_modal(severity="blocking") is True
    assert should_display_modal(severity="error") is False
    assert should_display_modal(severity="warning", irreversible_action=True) is True


def test_sanitize_user_error_replaces_technical_payload():
    assert sanitize_user_error('Traceback File "app.py" line 12 ValueError') == "An unexpected system issue occurred."


class _FakeWidget:
    def __init__(self, manager):
        self._manager = manager

    def winfo_manager(self):
        return self._manager


class _FakeParent:
    def __init__(self, children=None):
        self.calls = []
        self.children = children or []

    def after(self, delay_ms, callback):
        self.calls.append(("after", delay_ms))
        self.callback = callback
        return "timer-1"

    def after_cancel(self, timer_id):
        self.calls.append(("after_cancel", timer_id))

    def winfo_children(self):
        return self.children


class _FakeFrame:
    def __init__(self):
        self._mapped = False
        self.pack_kwargs = None

    def pack(self, **kwargs):
        self.pack_kwargs = kwargs
        self._mapped = True

    def pack_forget(self):
        self._mapped = False

    def winfo_ismapped(self):
        return self._mapped


class _FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


def test_main_gui_warning_presenter_can_show_and_dismiss_without_blocking():
    frame = _FakeFrame()
    parent = _FakeParent(children=[frame, _FakeWidget("grid"), _FakeWidget("pack")])
    message_var = _FakeVar()
    presenter = MainGuiWarningPresenter(
        parent=parent,
        frame=frame,
        message_var=message_var,
        message_label=object(),
        dismiss_button=object(),
    )

    presenter.show("Heads up", auto_expire_ms=1500)
    assert frame.winfo_ismapped() is True
    assert message_var.value == "Heads up"
    assert presenter._after_id == "timer-1"
    assert frame.pack_kwargs["before"] is parent.children[2]

    presenter.dismiss()
    assert frame.winfo_ismapped() is False
    assert message_var.value == ""


def test_present_transcription_partial_warning_uses_exact_copy() -> None:
    frame = _FakeFrame()
    parent = _FakeParent(children=[_FakeWidget("grid"), frame])
    message_var = _FakeVar()
    presenter = MainGuiWarningPresenter(
        parent=parent,
        frame=frame,
        message_var=message_var,
        message_label=object(),
        dismiss_button=object(),
    )

    shown = present_transcription_partial_warning(presenter, auto_expire_ms=500)

    assert shown == TRANSCRIPTION_PARTIAL_WARNING_COPY
    assert message_var.value == TRANSCRIPTION_PARTIAL_WARNING_COPY


def test_main_gui_warning_presenter_packs_without_before_when_no_pack_sibling() -> None:
    frame = _FakeFrame()
    parent = _FakeParent(children=[frame, _FakeWidget("grid")])
    message_var = _FakeVar()
    presenter = MainGuiWarningPresenter(
        parent=parent,
        frame=frame,
        message_var=message_var,
        message_label=object(),
        dismiss_button=object(),
    )

    presenter.show("Heads up", auto_expire_ms=None)

    assert "before" not in frame.pack_kwargs


class _FakePackTarget(_FakeWidget):
    def __init__(self, manager, *, packed=True):
        super().__init__(manager)
        self._packed = packed


class _StrictPackFrame(_FakeFrame):
    def pack(self, **kwargs):
        before = kwargs.get("before")
        if before is not None and not getattr(before, "_packed", True):
            raise tk.TclError("pack target is not packed")
        super().pack(**kwargs)


def test_main_gui_warning_presenter_finalize_warning_packing_falls_back_when_before_unpacked() -> None:
    frame = _StrictPackFrame()
    unpacked_target = _FakePackTarget("pack", packed=False)
    parent = _FakeParent(children=[frame, unpacked_target])
    message_var = _FakeVar()
    presenter = MainGuiWarningPresenter(
        parent=parent,
        frame=frame,
        message_var=message_var,
        message_label=object(),
        dismiss_button=object(),
    )

    presenter.show("Finalize warning", auto_expire_ms=777)

    assert frame.winfo_ismapped() is True
    assert frame.pack_kwargs["fill"] == "x"
    assert "before" not in frame.pack_kwargs
    assert message_var.value == "Finalize warning"
    assert presenter._after_id == "timer-1"
