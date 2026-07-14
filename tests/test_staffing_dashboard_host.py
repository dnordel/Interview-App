from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from candidate_report import CandidateReportRepository
from staffing_dashboard_host import StaffingDashboardAccess, StaffingDashboardHost
from staffing_service import StaffingService
from staffing_store import StaffingStore


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qt():
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    return qt_core, qt_gui, qt_widgets, app


def _store(tmp_path: Path, name: str = "staffing.sqlite3") -> StaffingStore:
    store = StaffingStore(tmp_path / name)
    store.initialize()
    return store


def _report_repository(tmp_path: Path, report_path: Path) -> CandidateReportRepository:
    repository = CandidateReportRepository(tmp_path / "interview_history.sqlite3")
    repository.initialize()
    snapshot = {
        "schema_version": 1,
        "history_id": "hist-shared",
        "candidate": {
            "candidate_name": "Jordan Lee",
            "school": "Hawthorne",
            "track": "Preschool",
            "interview_date": "2026-07-05",
            "qualification": {},
        },
        "questions": [],
        "scoring": {
            "weighted_total": 0,
            "max_weighted_total": 0,
            "percent_of_max": 0,
            "outcome": "Hire",
            "rows": [],
        },
        "summaries": {
            "executive_summary": "",
            "strengths": [],
            "concerns": [],
            "follow_up_items": [],
            "recommendation_rationale": "",
            "review_needed": False,
        },
        "report_path": str(report_path),
    }
    with sqlite3.connect(repository.db_path) as conn:
        CandidateReportRepository.insert_initial_on_connection(
            conn,
            "hist-shared",
            snapshot,
            actor="admin-user",
            actor_role="admin",
        )
        conn.commit()
    return repository


def _host(
    tmp_path: Path,
    *,
    role: str,
    store: StaffingStore,
    history_path: Path | None = None,
    open_document=None,
) -> StaffingDashboardHost:
    qt_core, qt_gui, qt_widgets, _app = _qt()
    parent = qt_widgets.QWidget()
    return StaffingDashboardHost(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        parent=parent,
        store=store,
        service_factory=lambda: StaffingService(store),
        access=StaffingDashboardAccess(
            role=role,
            actor=f"{role}-user",
            school_scope="Hawthorne" if role == "director" else "",
        ),
        history_path=history_path or tmp_path / "interview_history.sqlite3",
        notification_store_path=tmp_path / f"{role}-notifications.sqlite3",
        open_document=open_document,
    )


def test_access_config_is_immutable_normalized_and_rejects_unknown_role() -> None:
    access = StaffingDashboardAccess(role="DIRECTOR", actor="", school_scope=" Hawthorne ")

    assert access.role == "director"
    assert access.actor == "director"
    assert access.school_scope == "Hawthorne"
    assert access.removal_source == "director_staffing_dashboard"
    with pytest.raises(ValueError, match="admin or director"):
        StaffingDashboardAccess(role="owner", actor="owner")
    with pytest.raises(Exception):
        access.role = "admin"


def test_admin_and_director_hosts_share_v2_widget_and_native_actions(tmp_path: Path) -> None:
    qt_core, _qt_gui, qt_widgets, app = _qt()
    object_names: list[set[str]] = []
    for role in ("admin", "director"):
        store = _store(tmp_path, f"{role}.sqlite3")
        result = StaffingService(store).add_position(
            school="Hawthorne",
            classroom="Harmony 1",
            position_name="Teacher 1",
            position_type="Teacher",
            initial_status="need_now",
        )
        host = _host(tmp_path, role=role, store=store)
        assert host.widget is host.page.widget
        host.page._show_position_drawer(result.assignment_id)
        button = host.widget.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkDontNeed")
        assert button is not None and button.isEnabled()
        object_names.append(
            {
                widget.objectName()
                for widget in host.widget.findChildren(qt_widgets.QWidget)
                if widget.objectName()
            }
        )
        host.widget.close()
        host.parent.close()
        app.processEvents()

    assert object_names[0] == object_names[1]
    assert "StaffingV2PendingCandidateReportLink" not in object_names[0]


def test_native_mark_not_needed_works_without_entrypoint_callback(tmp_path: Path, monkeypatch) -> None:
    _qt_core, _qt_gui, qt_widgets, app = _qt()
    store = _store(tmp_path)
    result = StaffingService(store).add_position(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        initial_status="need_now",
    )
    host = _host(tmp_path, role="director", store=store)
    host.page._show_position_drawer(result.assignment_id)
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )

    host.widget.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkDontNeed").click()
    app.processEvents()

    assert store.get_assignment(result.assignment_id).status == "dont_need_now"
    host.widget.close()
    host.parent.close()


def test_shared_report_opener_enforces_scope_and_opens_word_for_both_roles(tmp_path: Path, monkeypatch) -> None:
    _qt_core, _qt_gui, qt_widgets, app = _qt()
    report_path = tmp_path / "Jordan.docx"
    report_path.write_bytes(b"test")
    repository = _report_repository(tmp_path, report_path)
    opened: list[Path] = []
    warnings: list[str] = []
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )

    for role in ("admin", "director"):
        host = _host(
            tmp_path,
            role=role,
            store=_store(tmp_path, f"report-{role}.sqlite3"),
            history_path=repository.db_path,
            open_document=opened.append,
        )
        host.open_candidate_report("hist-shared", "Hawthorne")
        dialog = host.candidate_report_dialog
        assert dialog is not None and dialog.role == role
        dialog.findChild(qt_widgets.QPushButton, "CandidateReportOpenWordButton").click()
        app.processEvents()
        assert opened[-1] == report_path.resolve()
        if role == "director":
            assert dialog.findChild(qt_widgets.QPushButton, "CandidateReportFinalizeButton").isVisible() is False
            host.open_candidate_report("hist-shared", "Palmdale")
            assert warnings[-1] == "Candidate report is outside the director school scope."
        dialog.close()
        host.widget.close()
        host.parent.close()
        app.processEvents()


def test_shared_legacy_report_open_failure_surfaces_warning(tmp_path: Path, monkeypatch) -> None:
    _qt_core, _qt_gui, qt_widgets, _app = _qt()
    history_path = tmp_path / "interview_history.sqlite3"
    with sqlite3.connect(history_path) as conn:
        conn.execute(
            "CREATE TABLE interview_history (row_key TEXT PRIMARY KEY, history_id TEXT, payload_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO interview_history VALUES (?, ?, ?)",
            (
                "legacy",
                "legacy",
                '{"school":"Hawthorne","interview_notes_path":"missing.docx"}',
            ),
        )
        conn.commit()
    warnings: list[str] = []
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    host = _host(
        tmp_path,
        role="director",
        store=_store(tmp_path),
        history_path=history_path,
    )

    host.open_candidate_report("legacy", "Hawthorne")

    assert warnings and "missing or invalid" in warnings[-1].lower()
    assert host.candidate_report_dialog is None
