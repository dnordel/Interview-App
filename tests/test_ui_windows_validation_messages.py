from src.ui_windows import SettingsWindow


class DummyVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class DummyChild:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class DummyFrame:
    def __init__(self) -> None:
        self.children: list[DummyChild] = []

    def winfo_children(self) -> list[DummyChild]:
        return self.children


class DummyLabel:
    def __init__(self, frame: DummyFrame) -> None:
        self.frame = frame

    def pack(self, **_kwargs: object) -> None:
        self.frame.children.append(DummyChild())


class DummyWidget:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.focused = False

    def winfo_exists(self) -> bool:
        return self.exists

    def focus_set(self) -> None:
        self.focused = True


def _new_settings_window() -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._TAB_TEMPLATES = "templates"
    window._TAB_SECURITY = "security"
    window._TAB_NOTIFICATIONS = "notifications"
    window._TAB_DEEPSEEK = "deepseek"
    window._tab_order = ["templates", "notifications", "deepseek", "security"]
    return window


def test_validation_errors_return_structured_items_with_guidance() -> None:
    window = _new_settings_window()
    window.whisper_temperature_var = DummyVar("bad")
    window.endpoint_var = DummyVar("ftp://example")
    window._settings_template_values = lambda: {"director_subject": "Hello {bad_token}"}
    window._settings_template_contexts = lambda: {"director_subject": "director"}
    window._deepseek_prompt_template_values = lambda: {
        "answer_summary_system": "system",
        "answer_summary_user": "Answer {payload_json}",
        "executive_summary_system": "system",
        "executive_summary_user": "Executive {answer_summaries_json}",
        "trait_suggestion_system": "system",
        "trait_suggestion_user": "Suggest {payload_json}",
        "trait_scoring_system": "system",
        "trait_scoring_user": "Score {payload_json}",
        "answer_summary_user_by_question": {},
        "trait_suggestion_user_by_question": {},
        "trait_scoring_user_by_question": {},
    }

    errors = window._validation_errors()

    template_issue = errors["templates"][0]
    endpoint_issue = errors["notifications"][0]
    assert template_issue["field"] == "director_subject"
    assert "Open Placeholders picker" in template_issue["guidance"]
    assert endpoint_issue["field"] == "director_referral_endpoint"
    assert "Use <https://...>" in endpoint_issue["guidance"]


def test_validation_errors_require_deepseek_per_question_prompt_json_and_payload_placeholder() -> None:
    window = _new_settings_window()
    window.whisper_temperature_var = DummyVar("0.0")
    window.endpoint_var = DummyVar("")
    window._settings_template_values = lambda: {}
    window._settings_template_contexts = lambda: {}
    window._deepseek_prompt_template_values = lambda: {
        "answer_summary_system": "system",
        "answer_summary_user": "Answer {payload_json}",
        "executive_summary_system": "system",
        "executive_summary_user": "Executive {answer_summaries_json}",
        "trait_suggestion_system": "system",
        "trait_suggestion_user": "Suggest {payload_json}",
        "trait_scoring_system": "system",
        "trait_scoring_user": "Score {payload_json}",
        "answer_summary_user_by_question": '{"custom_why_lpl": "Missing payload"}',
        "trait_suggestion_user_by_question": "{bad json",
        "trait_scoring_user_by_question": {"trait_1": "Score trait {payload_json}"},
    }

    errors = window._validation_errors()

    deepseek_fields = [item["field"] for item in errors["deepseek"]]
    assert "deepseek_answer_summary_user_by_question" in deepseek_fields
    assert "deepseek_trait_suggestion_user_by_question" in deepseek_fields


def test_normalize_deepseek_question_prompt_json_accepts_mapping_or_json() -> None:
    assert SettingsWindow._normalize_deepseek_question_prompt_json({"trait_1": "Prompt {payload_json}"}) == {
        "trait_1": "Prompt {payload_json}"
    }
    assert SettingsWindow._normalize_deepseek_question_prompt_json('{"2": "Prompt {payload_json}"}') == {
        "2": "Prompt {payload_json}"
    }


def test_apply_validation_messages_sets_tab_summary_and_field_guidance() -> None:
    window = _new_settings_window()
    window._tab_message_vars = {key: DummyVar("") for key in window._tab_order}
    window._field_error_vars = {
        "director_subject": DummyVar(""),
        "director_referral_endpoint": DummyVar(""),
    }
    window._field_focus_targets = {
        "director_subject": DummyWidget(),
        "director_referral_endpoint": DummyWidget(),
    }
    window._tab_summary_frames = {key: DummyFrame() for key in window._tab_order}
    window._wrapped_label = lambda frame, **_kwargs: DummyLabel(frame)

    errors = {
        "templates": [
            {
                "field": "director_subject",
                "message": "Contains unsupported template placeholders.",
                "guidance": "Open Placeholders picker and replace unsupported token.",
            }
        ],
        "notifications": [
            {
                "field": "director_referral_endpoint",
                "message": "Director referral endpoint must start with http:// or https://.",
                "guidance": "Use <https://...> (or <http://...>) for the endpoint URL.",
            }
        ],
        "security": [],
    }

    invalid_tabs, first_invalid_field = window._apply_validation_messages(errors)

    assert invalid_tabs == ["templates", "notifications"]
    assert first_invalid_field == "director_subject"
    assert "Open Placeholders picker" in window._field_error_vars["director_subject"].get()
    assert len(window._tab_summary_frames["templates"].winfo_children()) == 1


def test_focus_field_targets_only_existing_widgets() -> None:
    window = _new_settings_window()
    valid_widget = DummyWidget(exists=True)
    missing_widget = DummyWidget(exists=False)
    window._field_focus_targets = {
        "valid": valid_widget,
        "missing": missing_widget,
    }

    window._focus_field("valid")
    window._focus_field("missing")
    window._focus_field("unknown")

    assert valid_widget.focused is True
    assert missing_widget.focused is False


def test_high_risk_toggle_reverts_when_not_confirmed() -> None:
    window = _new_settings_window()
    window._high_risk_toggle_guard = False
    window.send_on_finalize_var = DummyVar("1")
    window._confirm_high_risk_toggle_enabled = lambda **_kwargs: False

    window._on_send_on_finalize_toggled()

    assert window.send_on_finalize_var.get() is False


def test_high_risk_toggle_keeps_value_when_confirmed() -> None:
    window = _new_settings_window()
    window._high_risk_toggle_guard = False
    window.send_on_finalize_var = DummyVar("1")
    window._confirm_high_risk_toggle_enabled = lambda **_kwargs: True

    window._on_send_on_finalize_toggled()

    assert bool(window.send_on_finalize_var.get()) is True


def test_high_risk_toggle_guard_skips_confirmation() -> None:
    window = _new_settings_window()
    window._high_risk_toggle_guard = True
    window.send_on_finalize_var = DummyVar(True)
    called = {"confirm": 0}

    def _confirm(**_kwargs: object) -> bool:
        called["confirm"] += 1
        return False

    window._confirm_high_risk_toggle_enabled = _confirm

    window._on_send_on_finalize_toggled()

    assert called["confirm"] == 0
    assert bool(window.send_on_finalize_var.get()) is True


def test_high_risk_toggle_confirmation_uses_messagebox(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_askyesno(title: str, detail: str) -> bool:
        captured["title"] = title
        captured["detail"] = detail
        return True

    monkeypatch.setattr("src.ui_windows.messagebox.askyesno", _fake_askyesno)

    confirmed = SettingsWindow._confirm_high_risk_toggle_enabled("Confirm", "Details")

    assert confirmed is True
    assert captured == {"title": "Confirm", "detail": "Details"}


class DummyMetricsLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def log_ux_validation_error(self, **payload: object) -> None:
        self.calls.append(("validation", payload))

    def log_ux_completion(self, **payload: object) -> None:
        self.calls.append(("completion", payload))

    def log_ux_click(self, **payload: object) -> None:
        self.calls.append(("click", payload))


def test_log_telemetry_routes_events_and_sanitizes_values() -> None:
    window = _new_settings_window()
    logger = DummyMetricsLogger()
    window.app = type("DummyApp", (), {"metrics_logger": logger})()

    window._log_telemetry("settings_validation_failed", tab_count=2, secret_value="redacted")
    window._log_telemetry("settings_saved", issues=0)
    window._log_telemetry("settings_tab_viewed", tab="security")

    assert logger.calls[0][0] == "validation"
    assert "secret_value" not in logger.calls[0][1]
    assert logger.calls[1][0] == "completion"
    assert logger.calls[2] == (
        "click",
        {"app": "interview", "surface": "settings", "target": "settings_tab_viewed", "tab": "security"},
    )
