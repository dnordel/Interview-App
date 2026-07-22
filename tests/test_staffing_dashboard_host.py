from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from datetime import date, timedelta

from candidate_report import CandidateReportRepository
from data_store import InterviewHistoryStore
from onboarding_sync import OnboardingSyncConflict
from onboarding_pilot_gate import REQUIRED_PILOT_SCENARIOS, approve_rollout, record_pilot_day
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
    director_school: str = "Hawthorne",
    onboarding_pilot_schools: tuple[str, ...] = ("Palmdale",),
    onboarding_rollout_path: Path | None = None,
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
            school_scope=director_school if role == "director" else "",
        ),
        history_path=history_path or tmp_path / "interview_history.sqlite3",
        notification_store_path=tmp_path / f"{role}-notifications.sqlite3",
        onboarding_pilot_schools=onboarding_pilot_schools,
        onboarding_rollout_path=onboarding_rollout_path,
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


def test_host_registers_admin_and_director_onboarding_navigation(tmp_path: Path) -> None:
    admin = _host(tmp_path, role="admin", store=_store(tmp_path, "admin-onboarding.sqlite3"))
    director = _host(
        tmp_path,
        role="director",
        store=_store(tmp_path, "director-onboarding.sqlite3"),
        director_school="Palmdale",
    )
    non_pilot = _host(tmp_path, role="director", store=_store(tmp_path, "director-nonpilot.sqlite3"))

    assert set(admin.page.external_pages) == {
        "onboarding_tasks",
        "onboarding_overview",
        "onboarding_employees",
        "onboarding_templates",
        "onboarding_communications",
    }
    assert set(director.page.external_pages) == {
        "onboarding_tasks",
        "onboarding_overview",
        "onboarding_employees",
        "onboarding_communications",
    }
    assert set(non_pilot.page.external_pages) == set()


def test_host_defers_onboarding_workspace_until_first_navigation(tmp_path: Path) -> None:
    host = _host(tmp_path, role="admin", store=_store(tmp_path, "lazy-onboarding.sqlite3"))

    assert host.onboarding_store is None
    assert host.onboarding_workspace is None
    assert host.page.external_pages["onboarding_tasks"] is None

    host.page.show_external_page("onboarding_tasks")
    first_workspace = host.onboarding_workspace
    assert first_workspace is not None
    assert host.onboarding_store is not None
    assert host.page.external_pages["onboarding_tasks"] is not None

    host.page.show_external_page("onboarding_overview")
    assert host.onboarding_workspace is first_workspace


def test_host_enables_hawthorne_only_after_recorded_rollout_approval(tmp_path: Path) -> None:
    evidence = tmp_path / "onboarding" / "pilot" / "evidence.jsonl"
    monday = date(2026, 7, 20)
    for offset in range(5):
        record_pilot_day(
            evidence, business_date=monday + timedelta(days=offset),
            device_id=f"device-{offset % 2}", scenarios=REQUIRED_PILOT_SCENARIOS,
            defects=(),
        )
    approve_rollout(
        evidence, school="Hawthorne", actor="admin",
        confirm_no_critical_high=True, reason="Palmdale gate passed",
    )

    host = _host(
        tmp_path, role="director", store=_store(tmp_path, "hawthorne-approved.sqlite3"),
        director_school="Hawthorne", onboarding_rollout_path=evidence,
    )

    assert host.ensure_onboarding_workspace() is not None


def test_admin_host_wires_shared_notification_directory_sender_and_scheduler(tmp_path: Path) -> None:
    admin = _host(tmp_path, role="admin", store=_store(tmp_path, "admin-communications.sqlite3"))
    workspace = admin.ensure_onboarding_workspace()

    assert workspace is not None
    assert workspace.admin_fallback_email == "recruiting@launchpadpreschool.com"
    assert workspace.reminder_recipient_resolver("Palmdale", "Payroll") == (
        "payroll@launchpadpreschool.com"
    )
    assert admin.onboarding_scheduler is not None
    assert workspace.service.artifact_vault is not None
    assert workspace.service.artifact_vault.root == (tmp_path / "onboarding" / "vault").resolve()


def test_host_uses_portable_onboarding_layout_and_migrates_legacy_replica(tmp_path: Path) -> None:
    legacy = tmp_path / "onboarding_palmdale.sqlite3"
    from onboarding_store import OnboardingStore

    OnboardingStore(legacy)
    director = _host(
        tmp_path,
        role="director",
        store=_store(tmp_path, "portable-director.sqlite3"),
        director_school="Palmdale",
    )

    expected = tmp_path / "onboarding" / "directors" / "palmdale.sqlite3"
    director.ensure_onboarding_workspace()
    assert director.onboarding_store is not None
    assert director.onboarding_store.path == expected
    assert expected.exists()
    assert legacy.exists()


@pytest.mark.parametrize("school", ["Palmdale", "Hawthorne", "North Long Beach"])
def test_each_canonical_launcher_scope_uses_its_own_authorized_replica(tmp_path: Path, school: str) -> None:
    host = _host(
        tmp_path,
        role="director",
        store=_store(tmp_path, f"{school}.staffing.sqlite3"),
        director_school=school,
        onboarding_pilot_schools=("Palmdale", "Hawthorne", "North Long Beach"),
    )
    workspace = host.ensure_onboarding_workspace()
    assert workspace is not None
    service = workspace.service

    assert service.access.school_scope == school
    assert host.onboarding_store.path == host.onboarding_paths.director_replica(school)
    other_school = "Hawthorne" if school != "Hawthorne" else "Palmdale"
    with pytest.raises(PermissionError, match="outside the director school scope"):
        service.create_employee(
            legal_name="Jordan Lee",
            school=other_school,
            role="Teacher",
            acceptance_date="2026-07-01",
            start_date="2026-07-15",
        )


def test_host_replays_shared_onboarding_changes_between_admin_and_director(tmp_path: Path) -> None:
    admin = _host(tmp_path, role="admin", store=_store(tmp_path, "sync-admin-staffing.sqlite3"))
    director = _host(
        tmp_path,
        role="director",
        store=_store(tmp_path, "sync-director-staffing.sqlite3"),
        director_school="Palmdale",
    )
    admin_workspace = admin.ensure_onboarding_workspace()
    director_workspace = director.ensure_onboarding_workspace()
    assert admin_workspace is not None
    assert director_workspace is not None
    employee = admin_workspace.service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )

    assert director.sync_onboarding() == 1
    assert director_workspace.service.get_employee(employee.id).school == "Palmdale"


def test_sync_conflict_prompt_identifies_versions_and_explicit_choices(tmp_path: Path, monkeypatch) -> None:
    _qt_core, _qt_gui, qt_widgets, _app = _qt()
    host = _host(tmp_path, role="admin", store=_store(tmp_path, "conflict-staffing.sqlite3"))
    observed: dict[str, str] = {}

    def answer(_parent, title, text, *_args):
        observed.update(title=title, text=text)
        return qt_widgets.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(qt_widgets.QMessageBox, "question", answer)
    accepted = host._resolve_onboarding_sync_conflict(
        OnboardingSyncConflict(
            event_id="event-1",
            source_replica="director:palmdale",
            entity_type="employee",
            entity_id="employee-1",
            school="Palmdale",
            fields=("notes",),
            local_version=3,
            incoming_version=4,
        )
    )

    assert accepted is True
    assert observed["title"] == "Onboarding Sync Conflict"
    assert "Employee employee-1" in observed["text"]
    assert "Local version: 3" in observed["text"]
    assert "Incoming version: 4" in observed["text"]
    assert "Yes — Use Incoming" in observed["text"]
    assert "No — Keep Local" in observed["text"]


def test_sync_conflict_defers_when_onboarding_edit_session_stays_open(tmp_path: Path, monkeypatch) -> None:
    _qt_core, _qt_gui, qt_widgets, _app = _qt()
    host = _host(tmp_path, role="admin", store=_store(tmp_path, "defer-conflict.sqlite3"))
    workspace = host.ensure_onboarding_workspace()
    assert workspace is not None
    workspace.request_navigation_away = lambda: False
    monkeypatch.setattr(
        qt_widgets.QMessageBox, "question",
        lambda *_args, **_kwargs: pytest.fail("conflict choice must wait for edit resolution"),
    )

    resolution = host._resolve_onboarding_sync_conflict(
        OnboardingSyncConflict(
            event_id="event-defer", source_replica="director:palmdale",
            entity_type="employee", entity_id="employee-1", school="Palmdale",
            fields=("notes",), local_version=1, incoming_version=2,
        )
    )

    assert resolution == "defer"


def test_host_close_guard_delegates_to_onboarding_workspace_and_stops_sync_after_accept(tmp_path: Path) -> None:
    host = _host(tmp_path, role="admin", store=_store(tmp_path, "close-guard.sqlite3"))
    workspace = host.ensure_onboarding_workspace()
    assert workspace is not None
    workspace.request_close = lambda: False
    assert host.request_onboarding_close() is False
    assert host.onboarding_sync_timer.isActive()

    workspace.request_close = lambda: True
    assert host.request_onboarding_close() is True
    assert host.onboarding_sync_timer.isActive()
    host.cleanup_onboarding()
    assert not host.onboarding_sync_timer.isActive()
    host.resume_onboarding()
    assert host.onboarding_sync_timer.isActive()


def test_director_notification_test_payloads_are_school_scoped(tmp_path: Path) -> None:
    history_path = tmp_path / "interview_history.sqlite3"
    history = InterviewHistoryStore(history_path)
    history.append({"history_id": "hawthorne", "candidate_name": "Ari Lane", "school": "Hawthorne", "interview_date": "2026-07-10"})
    history.append({"history_id": "palmdale", "candidate_name": "Sam Cruz", "school": "Palmdale", "interview_date": "2026-07-11"})
    store = _store(tmp_path)
    host = _host(tmp_path, role="director", store=store, history_path=history_path)

    payloads = host.notification_test_payloads("offer.approved")

    assert [choice.payload["history_id"] for choice in payloads] == ["hawthorne"]
    assert payloads[0].source_kind == "interview_history"
    assert host.notification_test_payloads("staffing.assignment.need_now") == []


def test_notification_test_payloads_include_curated_candidate_report_fields(tmp_path: Path) -> None:
    history_path = tmp_path / "interview_history.sqlite3"
    history = InterviewHistoryStore(history_path)
    snapshot = {
        "schema_version": 1,
        "history_id": "hist-rich",
        "candidate": {
            "candidate_name": "Jordan Lee",
            "school": "Hawthorne",
            "track": "Preschool",
            "interview_date": "2026-07-10",
            "qualification": {
                "degree_type": "BA",
                "ece_units_completed": 24,
                "years_experience": 5,
            },
        },
        "questions": [
            {"prompt": "Why preschool?", "transcript": "Because early learning matters."},
        ],
        "scoring": {"outcome": "Hire", "percent_of_max": 92},
        "report_path": "",
    }
    history.append_with_candidate_report(
        {
            "history_id": "hist-rich",
            "candidate_name": "Jordan Lee",
            "school": "Hawthorne",
            "interview_date": "2026-07-10",
            "position": "Lead Teacher",
        },
        snapshot,
        actor="admin-user",
    )
    store = _store(tmp_path)
    host = _host(tmp_path, role="director", store=store, history_path=history_path)

    payload = host.notification_test_payloads("offer.approved")[0].payload

    assert payload["candidate_name"] == "Jordan Lee"
    assert payload["position"] == "Lead Teacher"
    assert payload["degree"] == "BA"
    assert payload["ece_units"] == "24"
    assert payload["years_experience"] == "5"
    assert payload["score"] == "92"
    assert payload["interview_answer_1"] == "Because early learning matters."


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

    shared_admin = {name for name in object_names[0] if "onboarding" not in name.casefold()}
    shared_director = {name for name in object_names[1] if "onboarding" not in name.casefold()}
    assert shared_admin == shared_director
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
        assert report_path.read_bytes() == b"test"
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
