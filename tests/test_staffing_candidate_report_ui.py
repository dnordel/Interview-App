from __future__ import annotations

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
