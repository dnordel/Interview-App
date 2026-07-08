from ui_feedback import (
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


class FakePresenter:
    def __init__(self):
        self.calls = []

    def show(self, message, **kwargs):
        self.calls.append((message, kwargs))


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


def test_present_transcription_partial_warning_uses_exact_copy() -> None:
    presenter = FakePresenter()

    shown = present_transcription_partial_warning(presenter, auto_expire_ms=500)

    assert shown == TRANSCRIPTION_PARTIAL_WARNING_COPY
    assert presenter.calls == [(TRANSCRIPTION_PARTIAL_WARNING_COPY, {"auto_expire_ms": 500})]
