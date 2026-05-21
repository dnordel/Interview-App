from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "onboarding_app.pyw"
loader = SourceFileLoader("onboarding_app", str(MODULE_PATH))
spec = spec_from_loader(loader.name, loader)
assert spec and spec.loader
onboarding_app = module_from_spec(spec)
spec.loader.exec_module(onboarding_app)


class FakeEntry:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


class FakeDialog:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class FakeInlineValidation:
    def clear(self):
        return None


def test_save_employee_uses_inline_feedback_for_missing_name(monkeypatch):
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app._is_valid_date = lambda _: True

    captured = {}

    def _capture(_inline, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(onboarding_app, "show_inline_field_error", _capture)
    fields = {
        "Name": FakeEntry(""),
        "School": FakeEntry("School"),
        "Acceptance date (YYYY-MM-DD)": FakeEntry("2026-01-10"),
        "Start date (YYYY-MM-DD)": FakeEntry("2026-01-20"),
    }

    onboarding_app.OnboardingTrackerApp._save_employee(app, FakeDialog(), fields, FakeInlineValidation())

    assert captured["field_label"] == "Name"
    assert captured["focus_widget"] is fields["Name"]


def test_save_custom_template_uses_inline_feedback_for_invalid_numbers(monkeypatch):
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)

    captured = {}

    def _capture(_inline, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(onboarding_app, "show_inline_field_error", _capture)
    vars_map = {
        "title": FakeEntry("Orientation reminder"),
        "reference": FakeEntry("start_date"),
        "offset": FakeEntry("abc"),
        "cadence": FakeEntry("daily"),
        "interval": FakeEntry("1x"),
    }
    controls = {
        "title": object(),
        "offset": object(),
        "interval": object(),
    }

    onboarding_app.OnboardingTrackerApp._save_custom_template(
        app,
        FakeDialog(),
        vars_map,
        specific_date_picker=object(),
        inline_validation=FakeInlineValidation(),
        controls=controls,
    )

    assert captured["field_label"] == "Offset/interval"
    assert captured["focus_widget"] is controls["offset"]


def test_validate_placeholder_templates_for_run_sets_inline_global_message(monkeypatch):
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.state = type("State", (), {"email_settings": type("Email", (), {
        "reminder_subject_template": "Hello [unknown]",
        "reminder_body_template": "Body",
        "escalation_subject_template": "Esc",
        "escalation_body_template": "Body",
    })()})()

    captured = {}

    class FakeLogger:
        def log_ux_validation_error(self, **kwargs):
            captured["log"] = kwargs

    app.metrics_logger = FakeLogger()
    app._set_global_validation_message = lambda **kwargs: captured.update(kwargs)

    assert onboarding_app.OnboardingTrackerApp._validate_placeholder_templates_for_run(app) is False
    assert "message" in captured
    assert captured["severity"] == onboarding_app.VALIDATION_SEVERITY_ERROR


def test_run_reminders_now_uses_inline_for_invalid_recipients(monkeypatch):
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app._clear_global_validation_message = lambda: None
    app._validate_placeholder_templates_for_run = lambda: True
    app._runtime_recipient_validation_errors = lambda: ["invalid"]

    captured = {}

    class FakeLogger:
        def log_ux_validation_error(self, **kwargs):
            captured["log"] = kwargs

    app.metrics_logger = FakeLogger()
    app._set_global_validation_message = lambda **kwargs: captured.update(kwargs)

    onboarding_app.OnboardingTrackerApp.run_reminders_now(app)

    assert captured["severity"] == onboarding_app.VALIDATION_SEVERITY_ERROR
    assert "Recipients are invalid" in captured["message"]


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self._value = ""
        self._bindings = {}

    def grid(self, *args, **kwargs):
        return None

    def pack(self, *args, **kwargs):
        return None

    def columnconfigure(self, *args, **kwargs):
        return None

    def rowconfigure(self, *args, **kwargs):
        return None

    def bind(self, event, handler):
        self._bindings[event] = handler
        return None

    def configure(self, **kwargs):
        return None

    config = configure

    def insert(self, _index, value):
        self._value = value

    def get(self):
        return self._value


class _FakeDialog(_FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.destroyed = False
        self.protocols = {}

    def title(self, *_args, **_kwargs):
        return None

    def transient(self, *_args, **_kwargs):
        return None

    def grab_set(self):
        return None

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def destroy(self):
        self.destroyed = True

    def wait_window(self):
        return None


class _FakeStringVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeBooleanVar(_FakeStringVar):
    pass


def _install_basic_tk_mocks(monkeypatch):
    monkeypatch.setattr(onboarding_app.tk, "Toplevel", _FakeDialog)
    monkeypatch.setattr(onboarding_app.tk, "StringVar", _FakeStringVar)
    monkeypatch.setattr(onboarding_app.tk, "BooleanVar", _FakeBooleanVar)
    monkeypatch.setattr(onboarding_app.ttk, "Label", _FakeWidget)
    monkeypatch.setattr(onboarding_app.ttk, "Entry", _FakeWidget)
    monkeypatch.setattr(onboarding_app.ttk, "Frame", _FakeWidget)
    monkeypatch.setattr(onboarding_app.ttk, "Button", _FakeWidget)
    monkeypatch.setattr(onboarding_app.ttk, "Combobox", _FakeWidget)
    monkeypatch.setattr(onboarding_app.ttk, "LabelFrame", _FakeWidget)
    monkeypatch.setattr(onboarding_app.ttk, "Checkbutton", _FakeWidget)
    monkeypatch.setattr(onboarding_app, "DateEntry", _FakeWidget)


def test_prepare_modal_dialog_wires_escape_and_keyboard_navigation():
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.root = object()
    dialog = _FakeDialog()
    first = _FakeWidget()

    captured = {}

    def _capture_enable(_dialog, first_widget):
        captured["first_widget"] = first_widget

    app._enable_modal_keyboard_navigation = _capture_enable
    closed = {"count": 0}

    def _close_action():
        closed["count"] += 1

    onboarding_app.OnboardingTrackerApp._prepare_modal_dialog(app, dialog, first_widget=first, on_close=_close_action)

    assert captured["first_widget"] is first
    assert "<Escape>" in dialog._bindings
    dialog._bindings["<Escape>"](None)
    assert closed["count"] == 1
    dialog.protocols["WM_DELETE_WINDOW"]()
    assert closed["count"] == 2


def test_open_add_employee_dialog_uses_shared_modal_helper(monkeypatch):
    _install_basic_tk_mocks(monkeypatch)
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.root = object()
    app._add_employee_opened_at = 0

    class _Logger:
        def log_onboarding_canonical_event(self, *_args, **_kwargs):
            return None

    app.metrics_logger = _Logger()

    captured = {}

    def _capture_prepare(dialog, *, first_widget=None, on_close=None):
        captured["first_widget"] = first_widget
        captured["on_close"] = on_close

    app._prepare_modal_dialog = _capture_prepare

    onboarding_app.OnboardingTrackerApp.open_add_employee_dialog(app)

    assert captured["first_widget"] is not None
    assert callable(captured["on_close"])


def test_open_custom_template_dialog_uses_shared_modal_helper(monkeypatch):
    _install_basic_tk_mocks(monkeypatch)
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.root = object()

    captured = {}
    app._prepare_modal_dialog = lambda dialog, *, first_widget=None, on_close=None: captured.update({
        "first_widget": first_widget,
        "on_close": on_close,
    })
    app._toggle_specific_date_picker = lambda *_args, **_kwargs: None

    onboarding_app.OnboardingTrackerApp.open_custom_template_dialog(app)

    assert captured["first_widget"] is not None
    assert callable(captured["on_close"])


def test_open_email_settings_uses_shared_modal_helper(monkeypatch):
    _install_basic_tk_mocks(monkeypatch)
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.root = object()
    app.storage_dir = Path.cwd()
    app.state = type("State", (), {
        "email_settings": onboarding_app.EmailSettings(),
        "scheduler_settings": {},
    })()
    app._email_settings_controls = {"sender_email": _FakeWidget()}
    app._build_email_settings_fields = lambda *_args, **_kwargs: {}
    app._build_scheduler_settings_fields = lambda *_args, **_kwargs: None

    captured = {}
    app._prepare_modal_dialog = lambda dialog, *, first_widget=None, on_close=None: captured.update({
        "first_widget": first_widget,
        "on_close": on_close,
    })

    onboarding_app.OnboardingTrackerApp.open_email_settings(app)

    assert captured["first_widget"] is app._email_settings_controls["sender_email"]
    assert callable(captured["on_close"])


def test_show_presend_reminder_dialog_uses_shared_modal_helper(monkeypatch):
    _install_basic_tk_mocks(monkeypatch)
    monkeypatch.setattr(onboarding_app.tk, "Label", _FakeWidget)
    monkeypatch.setattr(onboarding_app.tk, "Text", _FakeWidget)

    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.root = object()
    app._presend_dialog_text = lambda _result: "preview"
    app._invoke_button_from_keyboard = lambda *_args, **_kwargs: None

    captured = {}
    app._prepare_modal_dialog = lambda dialog, *, first_widget=None, on_close=None: captured.update({
        "first_widget": first_widget,
        "on_close": on_close,
    })

    result = type("Result", (), {
        "recipients": {"reminder": [], "escalation": []},
        "task_breakdown": {},
        "counts": {"due_reminders": 0, "monthly_lines": 0},
        "escalation_candidates": [],
    })()

    choice = onboarding_app.OnboardingTrackerApp._show_presend_reminder_dialog(app, result)

    assert captured["first_widget"] is not None
    assert callable(captured["on_close"])
    assert choice == "cancel"
