from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from staffing_dashboard_v2 import StaffingDashboardV2Page
from staffing_service import StaffingService
from staffing_store import StaffingStore


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_pending_candidate_name_opens_report_without_changing_checkbox(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-report",
        candidate_name="Jordan Lee",
        school="Hawthorne",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        interview_date="2026-07-05",
    )
    opened: list[tuple[str, str]] = []
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
        candidate_report_open_callback=lambda history_id, school: opened.append((history_id, school)),
    )

    link = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2PendingCandidateReportLink")
    checkbox = page.widget.findChild(qt_widgets.QCheckBox, "StaffingV2DirectorInterviewCandidateSelect")
    assert link is not None
    assert checkbox is not None
    assert checkbox.property("directorReferralId") == candidate.id

    link.click()
    app.processEvents()

    assert opened == [("hist-report", "Hawthorne")]
    assert checkbox.isChecked() is False
    page.widget.close()


def test_completed_candidate_name_opens_structured_report(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-completed", candidate_name="Jordan Lee", school="Hawthorne",
        interviewer_rating=8.5, interviewer_outcome="hire", interview_date="2026-07-05",
    )
    service.record_director_interview(
        candidate.id, director_name="Avery", completed_date="2026-07-06", rating=4,
        decision="no_hire", decision_notes="Completed review.",
    )
    opened: list[tuple[str, str]] = []
    page = StaffingDashboardV2Page(
        QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets, store=store,
        service_factory=lambda: StaffingService(store),
        candidate_report_open_callback=lambda history_id, school: opened.append((history_id, school)),
    )

    link = page.widget.findChild(qt_widgets.QPushButton, "StaffingV2CompletedCandidateReportLink")
    assert link is not None
    table = page.widget.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewHistoryTable")
    assert table is not None
    assert link.text() == "Jordan Lee"
    assert table.item(0, 0).text() == ""
    assert table.horizontalHeaderItem(1).text() == "First Interview\nScore"
    assert table.horizontalHeaderItem(5).text() == "Proposed\nClassroom"
    assert [table.columnWidth(column) for column in range(table.columnCount())] == [
        200, 160, 110, 140, 120, 190, 190, 150,
    ]
    assert all(
        table.horizontalHeader().sectionResizeMode(column) == qt_widgets.QHeaderView.ResizeMode.Fixed
        for column in range(table.columnCount())
    )
    link.click()
    app.processEvents()

    assert opened == [("hist-completed", "Hawthorne")]
    page.widget.close()


def test_director_interview_dialog_uses_scrollable_two_column_layout(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-layout",
        candidate_name="Xandria Taylor",
        school="Palmdale",
        position="infant_toddler",
        interviewer_rating=8.0,
        interviewer_outcome="hire",
        interview_date="2026-07-17",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    assert dialog is not None
    scroll_area = dialog.findChild(qt_widgets.QScrollArea, "StaffingV2DirectorInterviewScrollArea")
    assert scroll_area is not None
    assert scroll_area.widgetResizable() is True

    grid = dialog.findChild(qt_widgets.QGridLayout, "StaffingV2DirectorInterviewFormGrid")
    assert grid is not None
    director_name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewDirectorName")
    shift_start = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewShiftStartText")
    assert director_name is not None
    assert shift_start is not None
    assert grid.getItemPosition(grid.indexOf(director_name.parentWidget()))[:2] == (0, 0)
    assert grid.getItemPosition(grid.indexOf(shift_start.parentWidget()))[:2] == (0, 1)

    save = dialog.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewSave")
    assert save is not None
    assert scroll_area.isAncestorOf(save) is False
    dialog.close()
    page.widget.close()


def test_director_interview_separates_school_positions_from_teacher_classrooms(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Harmony",
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher"},
                                    {"position_name": "Aide 1", "position_type": "Aide"},
                                ],
                            }
                        ],
                        "support_rows": [
                            {
                                "name": "Chef",
                                "slots": [{"position_name": "Chef", "position_type": "Support"}],
                            },
                            {
                                "name": "Swim Instructor",
                                "slots": [{"position_name": "Swim Instructor", "position_type": "Support"}],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store.import_seed_file(seed_path)
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-position-options",
        candidate_name="Jordan Lee",
        school="Palmdale",
        interviewer_outcome="hire",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    position = dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewOfferPosition")
    classroom = dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewClassroom")
    position_options = {
        position.itemData(index): position.itemText(index)
        for index in range(1, position.count())
    }

    assert position_options == {
        "lead_teacher": "Lead Teacher",
        "teacher": "Teacher",
        "teacher_floater": "Teacher/Floater",
        "aide": "Aide",
        "chef": "Chef",
        "swim_instructor": "Swim Instructor",
    }
    assert [classroom.itemText(index) for index in range(classroom.count())] == ["Harmony"]
    dialog.close()
    page.widget.close()


def test_director_interview_classroom_only_applies_to_teacher_positions(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    store.seed_assignment(
        school="Palmdale",
        classroom="Chef",
        position_name="Chef",
        position_type="Support",
    )
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-position-visibility",
        candidate_name="Jordan Lee",
        school="Palmdale",
        interviewer_outcome="hire",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    position = dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewOfferPosition")
    classroom = dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewClassroom")
    classroom_row = dialog.findChild(qt_widgets.QWidget, "StaffingV2DirectorInterviewClassroomRow")

    assert classroom_row.isVisible() is False
    position.setCurrentIndex(position.findData("teacher"))
    app.processEvents()
    assert classroom_row.isVisible() is True
    classroom.setCurrentText("Harmony")

    position.setCurrentIndex(position.findData("chef"))
    app.processEvents()
    assert classroom_row.isVisible() is False
    assert classroom.currentText() == ""

    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewDirectorName").setText("Director")
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewCandidateEmail").setText(
        "candidate@example.org"
    )
    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2DirectorInterviewNotes").setPlainText(
        "Strong fit for kitchen role."
    )
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewSave").click()
    app.processEvents()

    completed = service.list_completed_director_interviews(school="Palmdale")
    assert len(completed) == 1
    assert completed[0].offer_position_id == "chef"
    assert completed[0].proposed_classroom == ""
    assert dialog.isVisible() is False
    page.widget.close()


def test_director_interview_offer_position_does_not_overlap_missing_contact_fields(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-missing-contact",
        candidate_name="Jordan Lee",
        school="Palmdale",
        position="infant_toddler",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        interview_date="2026-07-21",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    grid = dialog.findChild(qt_widgets.QGridLayout, "StaffingV2DirectorInterviewFormGrid")
    row_names = (
        "StaffingV2DirectorInterviewOfferPositionRow",
        "StaffingV2DirectorInterviewCandidateEmailRow",
        "StaffingV2DirectorInterviewCandidatePhoneRow",
    )
    positions = [
        grid.getItemPosition(grid.indexOf(dialog.findChild(qt_widgets.QWidget, name)))[:2]
        for name in row_names
    ]

    assert positions == [(3, 1), (4, 1), (5, 1)]
    dialog.close()
    page.widget.close()


def test_director_interview_hire_with_missing_referral_contact_saves_from_dialog(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-save-missing-contact",
        candidate_name="Jordan Lee",
        school="Palmdale",
        position="infant_toddler",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        interview_date="2026-07-21",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewDirectorName").setText("Director")
    dialog.findChild(qt_widgets.QDoubleSpinBox, "StaffingV2DirectorInterviewRating").setValue(8.5)
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewClassroom").setCurrentText("Floater")
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewOfferPosition").setCurrentIndex(3)
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewCandidateEmail").setText(
        "candidate@example.org"
    )
    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2DirectorInterviewNotes").setPlainText(
        "Calm under pressure and gave strong transition examples."
    )
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewSave").click()
    app.processEvents()

    completed = service.list_completed_director_interviews(school="Palmdale")
    assert len(completed) == 1
    assert completed[0].offer_position_id == "teacher_floater"
    assert completed[0].decision_notes == "Calm under pressure and gave strong transition examples."
    assert dialog.isVisible() is False
    page.widget.close()


def test_director_interview_validation_error_stays_visible_outside_scroll_body(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-missing-email",
        candidate_name="Arlyn Molina",
        school="Palmdale",
        position="infant_toddler",
        interviewer_rating=8.0,
        interviewer_outcome="hire",
        interview_date="2026-07-17",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()
    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    assert dialog is not None
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewDirectorName").setText("Director")
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewOfferPosition").setCurrentIndex(1)
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewClassroom").setCurrentText("Chef")
    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2DirectorInterviewNotes").setPlainText("Hire.")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewSave").click()
    app.processEvents()

    error = dialog.findChild(qt_widgets.QLabel, "StaffingV2DirectorInterviewError")
    scroll_area = dialog.findChild(qt_widgets.QScrollArea, "StaffingV2DirectorInterviewScrollArea")
    pending_table = page.widget.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")
    history_table = page.widget.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewHistoryTable")
    assert error is not None
    assert scroll_area is not None
    assert pending_table is not None
    assert history_table is not None
    assert error.isVisible() is True
    assert "Candidate email is required" in error.text()
    assert scroll_area.isAncestorOf(error) is False
    assert dialog.isVisible() is True
    assert pending_table.rowCount() == 1
    assert history_table.rowCount() == 0
    dialog.close()
    page.widget.close()


def test_director_interview_prefills_director_from_current_school_director_assignment(tmp_path: Path):
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    store.seed_assignment(
        school="Palmdale",
        classroom="Director",
        position_name="Director",
        position_type="Director",
        status="filled",
        person_name="Edith",
    )
    service = StaffingService(store)
    candidate = service.upsert_director_candidate_referral(
        history_id="hist-director-default",
        candidate_name="Marina Gonzalez",
        school="Palmdale",
        position="infant_toddler",
        interviewer_rating=8.0,
        interviewer_outcome="hire",
        interview_date="2026-07-17",
        candidate_email="marina@example.org",
    )
    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: StaffingService(store),
    )

    page._open_director_interview_dialog(candidate.id)
    app.processEvents()

    dialog = page.widget.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    assert dialog is not None
    director_name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewDirectorName")
    assert director_name is not None
    assert director_name.text() == "Edith"
    dialog.close()
    page.widget.close()
