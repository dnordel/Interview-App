from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _settings_paths(tmp_path: Path):
    from admin_studio import AdminStudioPaths

    rubric = {
        "tracks": {"preschool": {"label": "Preschool", "active": True}},
        "traits": [
            {
                "id": "trait_1",
                "name": "Empathy",
                "priority": "critical",
                "weight": 3,
                "applicable_tracks": ["preschool"],
                "primary_question": "How do you comfort a child?",
                "descriptors": {str(score): f"Descriptor {score}" for score in range(1, 6)},
                "sample_answers": {str(score): f"Sample {score}" for score in range(1, 6)},
            }
        ],
    }
    overrides = {
        "track_trait_order": {},
        "trait_question_overrides": {},
        "custom_questions": {"preschool": []},
        "track_question_flow": {"preschool": [{"type": "trait", "id": "trait_1"}]},
    }
    files = {
        "rubric.json": rubric,
        "question_overrides.json": overrides,
        "school_offer_settings.json": {
            "Palmdale": {
                "full_time_template": "templates/full_time.docx",
                "part_time_template": "templates/part_time.docx",
                "contractor_template": "templates/contractor.docx",
                "offer_output_dir": "offers/Palmdale",
                "interview_notes_dir": "notes/Palmdale",
            }
        },
    }
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    return AdminStudioPaths(
        rubric_path=tmp_path / "rubric.json",
        overrides_path=tmp_path / "question_overrides.json",
        school_settings_path=tmp_path / "school_offer_settings.json",
        backup_dir=tmp_path / "backups",
    )


def _load_studio(paths):
    from admin_studio import AdminStudio

    return AdminStudio.load(paths)


def _page(tmp_path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from staffing_settings_v2 import StaffingSettingsV2Page

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    paths = _settings_paths(tmp_path)
    page = StaffingSettingsV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        studio=_load_studio(paths),
        email_settings_path=tmp_path / "email_account_settings.json",
    )
    page.widget.show()
    app.processEvents()
    return app, qt_widgets, page, paths


@pytest.mark.pyside_gui
def test_settings_page_uses_four_sections_and_responsive_navigation(tmp_path: Path) -> None:
    app, qt_widgets, page, _paths = _page(tmp_path)

    section_list = page.widget.findChild(qt_widgets.QListWidget, "StaffingSettingsV2SectionList")
    section_selector = page.widget.findChild(qt_widgets.QComboBox, "StaffingSettingsV2SectionSelector")
    assert [section_list.item(index).text() for index in range(section_list.count())] == [
        "Interview Flow",
        "Rubrics",
        "Templates & Folders",
        "Shared Email Account",
    ]
    assert section_list.isVisible()
    assert not section_selector.isVisible()

    page.widget.resize(860, 700)
    app.processEvents()
    assert not section_list.isVisible()
    assert section_selector.isVisible()
    page.widget.close()


@pytest.mark.pyside_gui
def test_settings_question_edit_updates_draft_immediately_in_global_edit_mode(tmp_path: Path) -> None:
    app, qt_widgets, page, _paths = _page(tmp_path)
    question_text = page.widget.findChild(qt_widgets.QPlainTextEdit, "StaffingSettingsV2QuestionText")
    edit_button = page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2StartEditing")

    assert question_text.isReadOnly()
    assert page.is_dirty is False
    edit_button.click()
    question_text.setPlainText("How would you help an upset child regulate?")
    app.processEvents()

    assert not question_text.isReadOnly()
    assert page.is_dirty is True
    assert page.draft.overrides["trait_question_overrides"]["trait_1"] == (
        "How would you help an upset child regulate?"
    )
    page.widget.close()


@pytest.mark.pyside_gui
def test_pyside_admin_settings_live_inside_staffing_v2_shell(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    import pyside_interview_app

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model_rubric_path = pyside_interview_app.DEFAULT_RUBRIC_PATH
    model_overrides_path = pyside_interview_app.QUESTIONS_OVERRIDE_PATH
    paths = _settings_paths(tmp_path)
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", paths.rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", paths.overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", paths.school_settings_path)
    monkeypatch.setattr(pyside_interview_app, "EMAIL_ACCOUNT_SETTINGS_PATH", tmp_path / "email.json")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing-seed.json")
    monkeypatch.setattr(pyside_interview_app.PySideInterviewWindow, "_run_due_notifications_safely", lambda self: None)
    model = pyside_interview_app.build_interview_redesign_model(
        rubric_path=model_rubric_path,
        overrides_path=model_overrides_path,
        history_path=tmp_path / "history.sqlite3",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    assert model.navigation == ["Staffing", "Staffing v2", "Onboarding"]
    assert "Admin" not in [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    assert window.staffing_v2_dashboard.settings_nav_button.isEnabled()
    assert not hasattr(window, "staffing_settings_v2_page")

    window.staffing_v2_dashboard.settings_nav_button.click()
    app.processEvents()
    assert window.staffing_v2_dashboard.current_page_id == "settings"
    assert window.staffing_settings_v2_page.widget is window.staffing_v2_dashboard.page_stack.currentWidget()
    assert window.staffing_v2_dashboard.page_stack.currentWidget().objectName() == "StaffingV2SettingsPage"
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_settings_rubric_editor_updates_and_duplicates_selected_trait(tmp_path: Path) -> None:
    app, qt_widgets, page, _paths = _page(tmp_path)
    page.section_list.setCurrentRow(1)
    page.edit_button.click()
    name = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2TraitName")
    signal_context = page.widget.findChild(qt_widgets.QLabel, "StaffingSettingsV2SignalContext")
    duplicate = page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2DuplicateTrait")

    assert "Descriptor 1" in signal_context.text()
    name.setText("Empathy and Co-Regulation")
    name.editingFinished.emit()
    duplicate.click()
    app.processEvents()

    assert page.draft.rubric["traits"][0]["name"] == "Empathy and Co-Regulation"
    assert [trait["id"] for trait in page.draft.rubric["traits"]] == ["trait_1", "trait_2"]
    assert page.is_dirty
    page.widget.close()


@pytest.mark.pyside_gui
def test_settings_rubric_editor_updates_weight_descriptors_and_signal_context(tmp_path: Path) -> None:
    app, qt_widgets, page, _paths = _page(tmp_path)
    page.section_list.setCurrentRow(1)
    page.edit_button.click()
    weight = page.widget.findChild(qt_widgets.QSpinBox, "StaffingSettingsV2TraitWeight")
    descriptor = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2TraitDescriptor_5")
    weight.setValue(5)
    descriptor.setText("Exceptional co-regulation evidence")
    descriptor.editingFinished.emit()
    app.processEvents()

    trait = page.draft.rubric["traits"][0]
    assert trait["weight"] == 5
    assert trait["descriptors"]["5"] == "Exceptional co-regulation evidence"
    assert "Exceptional co-regulation evidence" in page.signal_context.text()
    page.widget.close()


@pytest.mark.pyside_gui
def test_settings_template_paths_update_draft_and_show_validation(tmp_path: Path) -> None:
    app, qt_widgets, page, _paths = _page(tmp_path)
    page.section_list.setCurrentRow(2)
    page.edit_button.click()
    notes_dir = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2InterviewNotesDir")
    validation = page.widget.findChild(qt_widgets.QLabel, "StaffingSettingsV2Validation")

    notes_dir.setText(r"..\Candidates")
    notes_dir.editingFinished.emit()
    app.processEvents()

    assert page.draft.school_settings["Palmdale"]["interview_notes_dir"] == r"..\Candidates"
    assert "cannot contain '..'" in validation.text()
    assert not page.publish_button.isEnabled()
    page.widget.close()


def test_settings_shared_email_account_tests_and_saves_independently(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    import staffing_settings_v2

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    verified: list[str] = []
    saved_callbacks: list[str] = []
    monkeypatch.setattr(
        staffing_settings_v2,
        "verify_email_connection",
        lambda settings: verified.append(settings.smtp_host),
    )
    email_path = tmp_path / "email_account_settings.json"
    page = staffing_settings_v2.StaffingSettingsV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        studio=_load_studio(_settings_paths(tmp_path)),
        email_settings_path=email_path,
        on_email_settings_saved=lambda: saved_callbacks.append("saved"),
    )
    page.section_list.setCurrentRow(5)
    sender = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2EmailAddress")
    host = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2SmtpHost")
    username = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2SmtpUsername")
    password = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2SmtpPassword")
    sender.setText("admin@example.com")
    host.setText("smtp.example.com")
    username.setText("admin@example.com")
    password.setText("secret-value")
    app.processEvents()

    assert password.echoMode() == qt_widgets.QLineEdit.EchoMode.Password
    assert page.is_dirty
    page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2TestEmail").click()
    assert verified == ["smtp.example.com"]
    assert not email_path.exists()
    page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2SaveEmail").click()

    assert saved_callbacks == ["saved"]
    assert json.loads(email_path.read_text(encoding="utf-8"))["email"]["smtp_host"] == "smtp.example.com"
    assert page.is_dirty is False
    page.widget.close()


@pytest.mark.pyside_gui
def test_email_only_changes_do_not_enable_global_configuration_actions(tmp_path: Path) -> None:
    _app, qt_widgets, page, _paths = _page(tmp_path)
    sender = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2EmailAddress")
    sender.setText("unsaved@example.com")
    page.set_editing(True)

    assert page.is_dirty
    assert not page.draft.is_dirty
    assert not page.review_button.isEnabled()
    assert not page.publish_button.isEnabled()
    assert not page.discard_button.isEnabled()

    page.discard_button.click()
    assert sender.text() == "unsaved@example.com"
    assert page.is_dirty
    page.widget.close()


@pytest.mark.pyside_gui
def test_email_changes_participate_in_navigation_and_close_guards(tmp_path: Path, monkeypatch) -> None:
    _app, qt_widgets, page, _paths = _page(tmp_path)
    sender = page.widget.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2EmailAddress")
    sender.setText("draft@example.com")

    monkeypatch.setattr(page, "_ask_navigation_choice", lambda: "stay")
    assert page.request_navigation_away() is False
    assert sender.text() == "draft@example.com"
    monkeypatch.setattr(page, "_ask_navigation_choice", lambda: "keep")
    assert page.request_navigation_away() is True
    assert sender.text() == "draft@example.com"
    monkeypatch.setattr(page, "_ask_navigation_choice", lambda: "discard")
    assert page.request_navigation_away() is True
    assert sender.text() == ""
    assert not page.is_dirty

    sender.setText("close@example.com")
    monkeypatch.setattr(page, "_ask_close_choice", lambda: "stay")
    assert page.request_close() is False
    assert sender.text() == "close@example.com"
    monkeypatch.setattr(page, "_ask_close_choice", lambda: "discard")
    assert page.request_close() is True
    assert sender.text() == ""
    assert not page.is_dirty
    page.widget.close()


@pytest.mark.pyside_gui
def test_settings_publish_and_discard_use_confirmed_admin_draft(tmp_path: Path, monkeypatch) -> None:
    app, qt_widgets, page, paths = _page(tmp_path)
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )
    page.edit_button.click()
    question = page.widget.findChild(qt_widgets.QPlainTextEdit, "StaffingSettingsV2QuestionText")
    question.setPlainText("Published question text?")
    page.publish_button.click()
    app.processEvents()

    saved = json.loads(paths.overrides_path.read_text(encoding="utf-8"))
    assert saved["trait_question_overrides"]["trait_1"] == "Published question text?"
    assert page.is_dirty is False
    assert page.editing is False
    assert list(paths.backup_dir.glob("question_overrides.*.bak.json"))

    page.edit_button.click()
    question.setPlainText("Discard this text?")
    page.discard_button.click()
    assert page.is_dirty is False
    assert "Discard this text?" not in question.toPlainText()
    page.widget.close()


@pytest.mark.pyside_gui
def test_settings_dirty_navigation_and_close_guards_preserve_or_discard(tmp_path: Path, monkeypatch) -> None:
    _app, qt_widgets, page, _paths = _page(tmp_path)
    page.edit_button.click()
    question = page.widget.findChild(qt_widgets.QPlainTextEdit, "StaffingSettingsV2QuestionText")
    question.setPlainText("Unsaved question")

    monkeypatch.setattr(page, "_ask_navigation_choice", lambda: "stay")
    assert page.request_navigation_away() is False
    monkeypatch.setattr(page, "_ask_navigation_choice", lambda: "keep")
    assert page.request_navigation_away() is True
    assert page.is_dirty
    monkeypatch.setattr(page, "_ask_close_choice", lambda: "stay")
    assert page.request_close() is False
    monkeypatch.setattr(page, "_ask_close_choice", lambda: "discard")
    assert page.request_close() is True
    assert page.is_dirty is False
    page.widget.close()


@pytest.mark.pyside_gui
def test_settings_interview_flow_adds_duplicates_reorders_and_deletes_question(tmp_path: Path, monkeypatch) -> None:
    app, qt_widgets, page, _paths = _page(tmp_path)
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )
    page.edit_button.click()
    page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2AddQuestion").click()
    dialog = page.add_question_dialog
    dialog.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2NewQuestionId").setText("transition")
    dialog.findChild(qt_widgets.QLineEdit, "StaffingSettingsV2NewQuestionLabel").setText("Transition")
    dialog.findChild(qt_widgets.QPlainTextEdit, "StaffingSettingsV2NewQuestionText").setPlainText(
        "How do you guide classroom transitions?"
    )
    dialog.findChild(qt_widgets.QPushButton, "StaffingSettingsV2CreateQuestion").click()
    app.processEvents()

    flow = page.draft.overrides["track_question_flow"]["preschool"]
    assert flow[-1] == {"type": "custom", "id": "transition"}
    page.question_list.setCurrentRow(page.question_list.count() - 1)
    page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2DuplicateQuestion").click()
    assert flow[-1]["id"] == "transition-copy"
    page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2MoveQuestionUp").click()
    assert page.draft.overrides["track_question_flow"]["preschool"][-2]["id"] == "transition-copy"
    page.widget.findChild(qt_widgets.QPushButton, "StaffingSettingsV2DeleteQuestion").click()
    assert all(item["id"] != "transition-copy" for item in page.draft.overrides["track_question_flow"]["preschool"])
    page.widget.close()


def test_pyside_window_close_delegates_to_loaded_settings_guard() -> None:
    import pyside_interview_app

    window = pyside_interview_app.PySideInterviewWindow.__new__(pyside_interview_app.PySideInterviewWindow)

    class SettingsPage:
        def request_close(self) -> bool:
            return False

    window.staffing_settings_v2_page = SettingsPage()
    assert window._request_window_close() is False
    del window.staffing_settings_v2_page
    assert window._request_window_close() is True
