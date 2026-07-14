from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from candidate_report import CandidateReportRepository
from candidate_report_dialog import CandidateInterviewReportDialog
from staffing_service import StaffingService
from staffing_store import StaffingStore


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _repository(tmp_path: Path) -> CandidateReportRepository:
    repo = CandidateReportRepository(tmp_path / "interview_history.sqlite3")
    repo.initialize()
    snapshot = {
        "schema_version": 1,
        "history_id": "hist-dialog",
        "candidate": {
            "candidate_name": "Jordan Lee",
            "school": "Hawthorne",
            "track": "Preschool",
            "interview_date": "2026-07-05",
            "qualification": {"ece_units_completed": 24},
        },
        "questions": [
            {
                "question_id": "q1", "type": "trait", "title": "Reliability", "prompt": "Tell me about reliability.",
                "transcript": "Candidate answer", "original_transcript": "Candidate answer", "rating": 4, "weight": 2,
                "weighted_score": 8, "priority": "high", "skipped": False, "skip_reason": "",
                "absolute_disqualifier": False, "no_example_after_followups": False, "interviewer_notes": "Good example.",
            }
        ],
        "scoring": {"weighted_total": 8, "max_weighted_total": 10, "percent_of_max": 80.0, "outcome": "Hire", "rows": []},
        "summaries": {"executive_summary": "Strong candidate.", "strengths": ["Reliable"], "concerns": [], "follow_up_items": [], "recommendation_rationale": "Score-driven.", "review_needed": False},
        "report_path": str(tmp_path / "Jordan.docx"),
    }
    with sqlite3.connect(repo.db_path) as conn:
        CandidateReportRepository.insert_initial_on_connection(
            conn, "hist-dialog", snapshot, actor="admin-user", actor_role="admin"
        )
        conn.commit()
    return repo


def test_admin_dialog_matches_report_sections_and_has_no_editable_outcome(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        repository=_repository(tmp_path),
        history_id="hist-dialog",
        role="admin",
        actor="admin-user",
    )

    nav = dialog.findChild(qt_widgets.QListWidget, "CandidateReportSectionNavigation")
    labels = [nav.item(index).text() for index in range(nav.count())]
    assert labels == ["Overview", "Candidate Details", "Scores & Traits", "Interview Answers", "Summary & Outcome", "Audit History"]
    assert dialog.findChild(qt_widgets.QLabel, "CandidateReportCalculatedOutcome").text() == "Hire"
    assert dialog.findChild(qt_widgets.QComboBox, "CandidateReportFinalOutcome") is None
    assert dialog.findChild(qt_widgets.QPushButton, "CandidateReportReopenButton").isVisibleTo(dialog)
    assert dialog.findChild(qt_widgets.QPushButton, "CandidateReportSaveDraftButton").isEnabled() is False
    dialog.close()
    app.processEvents()


def test_admin_can_edit_skip_reason_and_approved_evaluation_flags(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=_repository(tmp_path), history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.reopen_initial("Correct evaluation")

    skipped = dialog.findChild(qt_widgets.QCheckBox, "CandidateReportSkipped_0")
    reason = dialog.findChild(qt_widgets.QLineEdit, "CandidateReportSkipReason_0")
    disqualifier = dialog.findChild(qt_widgets.QCheckBox, "CandidateReportDisqualifier_0")
    no_example = dialog.findChild(qt_widgets.QCheckBox, "CandidateReportNoExample_0")
    assert all(widget is not None for widget in (skipped, reason, disqualifier, no_example))
    skipped.setChecked(True)
    reason.setText("No usable answer after follow-ups")
    disqualifier.setChecked(True)
    no_example.setChecked(True)
    dialog._capture_widgets()

    question = dialog.working_snapshot["questions"][0]
    assert question["skipped"] is True
    assert question["skip_reason"] == "No usable answer after follow-ups"
    assert question["absolute_disqualifier"] is True
    assert question["no_example_after_followups"] is True
    dialog.dirty = False
    dialog.close()
    app.processEvents()


def test_rating_edit_immediately_recalculates_score_preview(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric = {
        "traits": [{"id": "q1", "name": "Reliability", "priority": "non-critical", "weight": 1, "applicable_tracks": ["all"], "primary_question": "Q1"}],
        "tracks": {"Preschool": {"label": "Preschool", "max_weighted_total": 5}},
    }
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=_repository(tmp_path), history_id="hist-dialog", role="admin", actor="admin-user", rubric=rubric,
    )
    dialog.reopen_initial("Correct score")
    rating = dialog.findChild(qt_widgets.QSpinBox)
    rating.setValue(1)

    preview = dialog.findChild(qt_widgets.QLabel, "CandidateReportScoreSummary")
    assert "20.0%" in preview.text()
    assert dialog.working_snapshot["scoring"]["percent_of_max"] == 20.0
    dialog.dirty = False
    dialog.close()
    app.processEvents()


def test_save_changes_validates_while_save_draft_accepts_incomplete_work(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    repo = _repository(tmp_path)
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=repo, history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.reopen_initial("Correct skip state")
    dialog.findChild(qt_widgets.QCheckBox, "CandidateReportSkipped_0").setChecked(True)

    dialog.findChild(qt_widgets.QPushButton, "CandidateReportSaveChangesButton").click()
    assert repo.load_visible_version("hist-dialog", role="admin").version_number == 2
    assert "Skipped scored question requires a reason" in dialog.findChild(qt_widgets.QLabel, "CandidateReportStatusMessage").text()

    dialog.findChild(qt_widgets.QPushButton, "CandidateReportSaveDraftButton").click()
    assert repo.load_visible_version("hist-dialog", role="admin").version_number == 3
    dialog.close()
    app.processEvents()


def test_field_and_report_revert_restore_saved_values_without_new_version(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    repo = _repository(tmp_path)
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=repo, history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.reopen_initial("Correct candidate details")
    name = dialog.findChild(qt_widgets.QLineEdit, "CandidateReportField_candidate_name")
    name.setText("Edited Name")
    marker = dialog.findChild(qt_widgets.QLabel, "CandidateReportEdited_candidate_name")
    field_revert = dialog.findChild(qt_widgets.QPushButton, "CandidateReportRevert_candidate_name")
    assert marker.isHidden() is False
    field_revert.click()
    assert name.text() == "Jordan Lee"

    name.setText("Another Name")
    dialog.findChild(qt_widgets.QPushButton, "CandidateReportRevertAllButton").click()
    assert dialog.findChild(qt_widgets.QLineEdit, "CandidateReportField_candidate_name").text() == "Jordan Lee"
    assert repo.load_visible_version("hist-dialog", role="admin").version_number == 2
    dialog.close()
    app.processEvents()


def test_validation_readiness_issue_navigates_to_missing_skip_reason(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=_repository(tmp_path), history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.reopen_initial("Correct skip state")
    dialog.findChild(qt_widgets.QCheckBox, "CandidateReportSkipped_0").setChecked(True)
    dialog.findChild(qt_widgets.QPushButton, "CandidateReportSaveChangesButton").click()

    readiness = dialog.findChild(qt_widgets.QFrame, "CandidateReportReadinessPanel")
    issue = dialog.findChild(qt_widgets.QPushButton, "CandidateReportValidationIssue_0")
    assert readiness.isHidden() is False
    assert "Skipped scored question requires a reason" in issue.text()
    reason = dialog.findChild(qt_widgets.QLineEdit, "CandidateReportSkipReason_0")
    assert "#dc2626" in reason.styleSheet()
    issue.click()
    assert dialog.navigation.currentItem().text() == "Interview Answers"
    assert dialog.focusWidget().objectName() == "CandidateReportSkipReason_0"
    dialog.dirty = False
    dialog.close()
    app.processEvents()


def test_answer_filters_and_expand_collapse_preserve_question_state(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=_repository(tmp_path), history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.reopen_initial("Correct answers")
    skipped = dialog.findChild(qt_widgets.QCheckBox, "CandidateReportSkipped_0")
    skipped.setChecked(True)
    filter_box = dialog.findChild(qt_widgets.QComboBox, "CandidateReportAnswerFilter")
    filter_box.setCurrentText("Skipped")
    card = dialog.findChild(qt_widgets.QFrame, "CandidateReportQuestionCard_0")
    assert card.isHidden() is False

    dialog.findChild(qt_widgets.QPushButton, "CandidateReportCollapseAll").click()
    assert dialog.findChild(qt_widgets.QWidget, "CandidateReportQuestionBody_0").isHidden()
    dialog.findChild(qt_widgets.QPushButton, "CandidateReportExpandAll").click()
    assert dialog.findChild(qt_widgets.QWidget, "CandidateReportQuestionBody_0").isHidden() is False
    assert skipped.isChecked()
    dialog.dirty = False
    dialog.close()
    app.processEvents()


def test_keyboard_shortcuts_save_navigate_and_focus_search(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_test = pytest.importorskip("PySide6.QtTest")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    repo = _repository(tmp_path)
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=repo, history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.show()
    dialog.activateWindow()
    dialog.reopen_initial("Keyboard correction")
    app.processEvents()
    name = dialog.findChild(qt_widgets.QLineEdit, "CandidateReportField_candidate_name")
    name.setText("Keyboard Name")
    name.setFocus()
    qt_test.QTest.keyClick(name, qt_core.Qt.Key.Key_S, qt_core.Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert repo.load_visible_version("hist-dialog", role="admin").version_number == 3

    qt_test.QTest.keyClick(dialog, qt_core.Qt.Key.Key_4, qt_core.Qt.KeyboardModifier.AltModifier)
    assert dialog.navigation.currentItem().text() == "Interview Answers"
    qt_test.QTest.keyClick(dialog, qt_core.Qt.Key.Key_F, qt_core.Qt.KeyboardModifier.ControlModifier)
    assert dialog.focusWidget().objectName() == "CandidateReportAnswerSearch"
    dialog.close()
    app.processEvents()


def test_dialog_has_window_controls_and_parent_scrim_lifecycle(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    parent = qt_widgets.QWidget()
    parent.resize(1200, 800)
    parent.show()
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets, parent=parent,
        repository=_repository(tmp_path), history_id="hist-dialog", role="admin", actor="admin-user",
    )
    dialog.show()
    app.processEvents()
    flags = dialog.windowFlags()
    assert flags & qt_core.Qt.WindowType.WindowMinimizeButtonHint
    assert flags & qt_core.Qt.WindowType.WindowMaximizeButtonHint
    scrim = parent.findChild(qt_widgets.QFrame, "CandidateReportDashboardScrim")
    assert scrim is not None and scrim.isVisibleTo(parent)

    dialog.close()
    app.processEvents()
    assert scrim.isVisible() is False
    parent.close()


def test_dialog_fits_supported_dashboard_viewports(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    for index, viewport in enumerate(((1366, 768), (1920, 1080))):
        parent = qt_widgets.QWidget()
        parent.resize(*viewport)
        dialog = CandidateInterviewReportDialog(
            QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets, parent=parent,
            repository=_repository(tmp_path / str(index)), history_id="hist-dialog", role="admin", actor="admin-user",
        )

        assert dialog.width() <= viewport[0] - 32
        assert dialog.height() <= viewport[1] - 32
        dialog.close()
        parent.close()
        app.processEvents()


def test_open_word_surfaces_os_error(tmp_path: Path) -> None:
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    report_path = tmp_path / "Jordan.docx"
    report_path.write_bytes(b"test")
    repository = _repository(tmp_path)
    repository.sync_report_path("hist-dialog", report_path)

    def fail_open(_path: Path) -> None:
        raise OSError("access denied")

    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        repository=repository,
        history_id="hist-dialog",
        role="director",
        actor="director-user",
        school_scope="Hawthorne",
        open_document=fail_open,
    )
    dialog._open_word()

    assert dialog.status_label.text() == "Saved Word report could not be opened: access denied"
    dialog.close()
    app.processEvents()


def test_open_word_rejects_missing_and_non_docx_paths(tmp_path: Path) -> None:
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        repository=_repository(tmp_path),
        history_id="hist-dialog",
        role="director",
        actor="director-user",
        school_scope="Hawthorne",
    )
    invalid_path = tmp_path / "Jordan.txt"
    invalid_path.write_text("not a Word report", encoding="utf-8")

    for path in (tmp_path / "missing.docx", invalid_path):
        dialog.working_snapshot["report_path"] = str(path)
        dialog._open_word()
        assert dialog.status_label.text() == "Saved Word report is missing or invalid."

    dialog.close()
    app.processEvents()


def test_director_reopened_section_edits_only_director_fields(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    referral = service.upsert_director_candidate_referral(
        history_id="hist-dialog", candidate_name="Jordan Lee", school="Hawthorne",
        interviewer_rating=8.0, interviewer_outcome="hire", interview_date="2026-07-05",
    )
    submitted = service.record_director_interview(
        referral.id, director_name="Avery", completed_date="2026-07-06", rating=4,
        decision="no_hire", decision_notes="Original notes.",
    )
    reopened = service.reopen_director_interview(
        submitted.id, expected_row_version=submitted.row_version, reason="Correct notes",
        actor="Avery", actor_role="director",
    )
    dialog = CandidateInterviewReportDialog(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets,
        repository=_repository(tmp_path), history_id="hist-dialog", role="director",
        actor="Avery", school_scope="Hawthorne", director_interview=reopened, director_service=service,
    )

    assert dialog.findChild(qt_widgets.QLineEdit, "CandidateReportField_candidate_name").isReadOnly()
    notes = dialog.findChild(qt_widgets.QPlainTextEdit, "CandidateReportDirectorNotes")
    save = dialog.findChild(qt_widgets.QPushButton, "CandidateReportSaveDirectorButton")
    assert notes is not None and notes.isReadOnly() is False
    assert save is not None and save.isEnabled()
    audit = dialog.findChild(qt_widgets.QTableWidget, "CandidateReportAuditTable")
    actions = [audit.item(row, 3).text() for row in range(audit.rowCount())]
    assert "Director Interview Reopened" in actions
    assert dialog.findChild(qt_widgets.QPushButton, "CandidateReportExportAuditButton") is not None
    audit.setCurrentCell(0, 0)
    assert "Revision ID:" in dialog.findChild(qt_widgets.QPlainTextEdit, "CandidateReportAuditDetailsText").toPlainText()
    dialog.close()
    app.processEvents()
