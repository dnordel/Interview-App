import json
import os
import queue
import sys
import threading
import wave
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pyside_interview_app
from data_store import InterviewHistoryStore
from docx import Document
from notification_models import NotificationRecipient, NotificationRule, NotificationSendResult
from notification_store import NotificationStore
from staffing_dashboard_v2 import (
    StaffingDashboardV2Page,
    _display_date,
    _validation_issues_from_rows,
    configure_v2_scroll_areas,
)
from staffing_models import StaffingMetricRow

from onboarding_operations import Employee, EmployeeTask
from pyside_interview_app import (
    PySideInterviewSession,
    build_interview_redesign_model,
    build_pyside_candidate_board,
    build_pyside_onboarding_board,
    latest_pyside_draft_path,
    standard_window_control_flags,
)
from scoring_reporting import CandidateQualification


def _pyside_window_on_page(model, page_name: str):
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index(page_name))
    window.QtWidgets.QApplication.processEvents()
    return window


def test_pyside_session_imports_indeed_transcript_for_rating_review(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Miriam", school="Palmdale", track_key="preschool")

    result = session.import_indeed_transcript_text(
        """
Speaker 1: So, Miriam, why are you applying specifically to our company, Launchpad?

Speaker 0: You are closer to home and the ratios look safe and intentional.

Speaker 1: Tell me about a time a child was having a hard moment emotionally.

Speaker 0: I got low, helped them breathe, and supported words for what happened.
""",
    )

    assert result.interviewer_speaker == "Speaker 1"
    assert result.candidate_speaker == "Speaker 0"
    assert result.mapped_count == 2
    assert session.answers["Why-LPL"]["notes"].startswith("You are closer")
    assert session.answers["trait_1"]["notes"].startswith("I got low")
    assert session.answers["trait_1"]["score"] == ""
    assert session.flow_candidate_transcripts[2].startswith("I got low")
    assert session.active_question().question_id == "trait_1"

def test_pyside_session_writes_interview_session_snapshots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_BASE_DIR", tmp_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Miriam", school="Palmdale", track_key="preschool")
    session.flow_candidate_transcripts[0] = "Candidate wants to work close to home."

    session.save_answer_and_advance(notes="Strong intro answer", score="")

    session_files = list((tmp_path / "interview_sessions").glob("*.json"))
    assert len(session_files) == 1
    payload = json.loads(session_files[0].read_text(encoding="utf-8"))
    assert payload["interview"]["candidate_name"] == "Miriam"
    assert payload["interview"]["interview_date"] == session.interview_date
    assert payload["questions"]["0"]["item_id"] == "intro_script"
    assert payload["questions"]["0"]["candidate_transcript"] == "Candidate wants to work close to home."
    assert "Company Statement" in payload["questions"]["0"]["notes"]["question_text"]

def test_pyside_session_imports_indeed_transcript_when_speaker_numbers_flip(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Chantelle", school="Palmdale", track_key="preschool")

    result = session.import_indeed_transcript_text(
        """
Speaker 0: Why are you applying specifically to our company, Launchpad?

Speaker 1: The school feels aligned with my experience and commute.

Speaker 0: Tell me about a time a child was having a hard moment emotionally.

Speaker 1: I noticed frustration, paused, and helped the child use words.
""",
    )

    assert result.interviewer_speaker == "Speaker 0"
    assert result.candidate_speaker == "Speaker 1"
    assert session.answers["trait_1"]["notes"].startswith("I noticed frustration")
    assert session.answers["trait_1"]["score"] == ""


def _widget_text(widget) -> str:
    from PySide6 import QtWidgets

    labels = widget.findChildren(QtWidgets.QLabel)
    return " ".join(label.text() for label in labels)


def _table_text(table) -> str:
    if table is None:
        return ""
    values: list[str] = []
    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is not None:
                values.append(item.text())
    return " ".join(values)


def _icon_has_primary_blue(icon, size: int = 18) -> bool:
    image = icon.pixmap(size, size).toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 0 and color.red() == 37 and color.green() == 99 and color.blue() == 235:
                return True
    return False

def test_staffing_v2_validation_helpers_hide_seed_dates_and_build_worklist() -> None:
    rows = [
        StaffingMetricRow(
            assignment_id=1,
            school="Hawthorne",
            classroom="Harmony 1",
            position_name="Teacher 1",
            position_type="Teacher",
            status="need_now",
            start_date="1970-01-01T00:00:00Z",
        ),
        StaffingMetricRow(
            assignment_id=2,
            school="Hawthorne",
            classroom="Harmony 1",
            position_name="Teacher 2",
            position_type="Teacher",
            status="filled",
            person_name="Stephanie",
            permit_status="no_permit_or_application",
        ),
    ]

    issues = _validation_issues_from_rows(rows)

    assert _display_date("1970-01-01T00:00:00Z") == "-"
    assert [issue["issue"] for issue in issues[:1]] == ["Unfilled Need Now position"]
    assert {issue["issue"] for issue in issues[1:]} == {
        "Placeholder start date",
        "Permit status needs review",
    }
    assert {issue["type"] for issue in issues} == {"Coverage", "Lifecycle", "Compliance"}
    assert issues[0]["severity"] == "Critical"

def test_staffing_v2_scroll_helper_relays_wheel_events_from_child_widgets() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    root = qt_widgets.QWidget()
    root_layout = qt_widgets.QVBoxLayout(root)
    scroll = qt_widgets.QScrollArea()
    scroll.setWidgetResizable(True)
    content = qt_widgets.QWidget()
    content_layout = qt_widgets.QVBoxLayout(content)
    first_child = qt_widgets.QLabel("Top row")
    content_layout.addWidget(first_child)
    for index in range(40):
        content_layout.addWidget(qt_widgets.QLabel(f"Scrollable row {index}"))
    scroll.setWidget(content)
    scroll.resize(220, 120)
    root_layout.addWidget(scroll)
    root.show()
    app.processEvents()

    configure_v2_scroll_areas(qt_widgets, root, qt_core)
    wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(10, 10),
        qt_core.QPointF(10, 10),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(first_child, wheel)
    app.processEvents()

    assert scroll.verticalScrollBar().value() > 0
    assert wheel.isAccepted()
    root.close()
    app.processEvents()

def test_staffing_v2_scroll_helper_relays_from_nested_item_view_without_range() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    root = qt_widgets.QWidget()
    root_layout = qt_widgets.QVBoxLayout(root)
    scroll = qt_widgets.QScrollArea()
    scroll.setWidgetResizable(True)
    content = qt_widgets.QWidget()
    content_layout = qt_widgets.QVBoxLayout(content)
    table = qt_widgets.QTableWidget(1, 1)
    table.setItem(0, 0, qt_widgets.QTableWidgetItem("Visible table row"))
    table.setFixedHeight(90)
    content_layout.addWidget(table)
    for index in range(40):
        content_layout.addWidget(qt_widgets.QLabel(f"Scrollable row {index}"))
    scroll.setWidget(content)
    scroll.resize(260, 140)
    root_layout.addWidget(scroll)
    root.show()
    app.processEvents()

    configure_v2_scroll_areas(qt_widgets, root, qt_core)
    wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(10, 10),
        qt_core.QPointF(10, 10),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(table.viewport(), wheel)
    app.processEvents()

    assert table.verticalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().value() > 0
    assert wheel.isAccepted()
    root.close()
    app.processEvents()

def test_redesign_model_prioritizes_guided_interview_workflow(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    overrides_path = tmp_path / "question_overrides.json"
    history_path = tmp_path / "interview_history.sqlite3"

    rubric_path.write_text(
        json.dumps(
            {
                "metadata": {"version": "test"},
                "scoring": {},
                "tracks": {"preschool": {"label": "Preschool", "max_weighted_total": 5}},
                "absolute_disqualifiers": ["Unsafe handling"],
                "traits": [
                    {
                        "id": "trait_1",
                        "name": "Empathy",
                        "priority": "Critical",
                        "weight": 1,
                        "applicable_tracks": ["preschool"],
                        "primary_question": "Tell me about a hard child moment.",
                        "descriptors": {
                            "1": "Serious concern",
                            "2": "Weak",
                            "3": "Mixed / acceptable",
                            "4": "Strong",
                            "5": "Excellent",
                        },
                        "sample_answers": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {
                    "preschool": [{"id": "Why-LPL", "text": "Why Launch Pad Learning?", "order": 1}]
                },
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "trait", "id": "trait_1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    InterviewHistoryStore(history_path).append(
        {
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "track": "Preschool",
            "score": 60.95,
            "status": "Finalized",
            "next_action": "Generate Offer",
        }
    )

    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=history_path,
        school_options=["Palmdale"],
    )

    assert model.app_title == "Interview Assistant"
    assert model.navigation == ["Interviews", "Candidates", "Offers", "Staffing", "Staffing v2", "Onboarding", "Admin"]
    assert model.home.primary_action == "Start a New Interview"
    assert model.home.admin_visible_on_home is False
    assert model.home.recent_interviews[0].next_action == "Generate Offer"
    assert history_path.exists()
    assert not history_path.with_suffix(".json").exists()
    assert model.setup_steps == ["Candidate", "Interview Plan", "Ready"]

    preschool_flow = model.flows["preschool"]
    assert [item.kind for item in preschool_flow.items] == ["custom", "trait"]
    assert preschool_flow.items[0].prompt == "Why Launch Pad Learning?"
    scored = preschool_flow.items[1]
    assert scored.score_cards[0].label == "1"
    assert scored.score_cards[0].description == "Serious concern"
    assert "Needs follow-up" in scored.quick_actions
    assert "Disqualifier observed" in scored.quick_actions

def test_pyside_model_accepts_revision_probe_and_gateway_fields(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    overrides_path = tmp_path / "question_overrides.json"
    history_path = tmp_path / "interview_history.sqlite3"

    rubric_path.write_text(
        json.dumps(
            {
                "metadata": {"version": "test"},
                "scoring": {},
                "tracks": {
                    "behavior_support_specialist": {
                        "label": "Behavior Support Specialist",
                        "max_weighted_total": 5,
                        "gateway_requirements": ["Meets schedule requirements"],
                    }
                },
                "absolute_disqualifiers": ["Unsafe handling"],
                "interviewer_guidance": {},
                "traits": [
                    {
                        "id": "bss_trait_1",
                        "name": "Behavior Support",
                        "priority": "Critical",
                        "weight": 1,
                        "applicable_tracks": ["behavior_support_specialist"],
                        "primary_question": "Tell me about behavior support.",
                        "follow_up_probes": ["What did you try first?", "What changed after that?"],
                        "descriptors": {
                            "1": "Serious concern. Unsafe or blaming.",
                            "2": "Weak. Thin or adult-centered.",
                            "3": "Mixed. Safe but basic.",
                            "4": "Strong. Practical and child-centered.",
                            "5": "Excellent. Reflective, specific, and preventive.",
                        },
                        "sample_answers": {
                            "1": "Low",
                            "2": "Weak",
                            "3": "Mixed",
                            "4": "Strong",
                            "5": "Excellent",
                        },
                    }
                ],
                "final_record_fields": ["Gateway Requirements Status"],
                "canonical_text": "canonical",
            }
        ),
        encoding="utf-8",
    )
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {},
                "track_question_flow": {
                    "behavior_support_specialist": [{"type": "trait", "id": "bss_trait_1"}]
                },
            }
        ),
        encoding="utf-8",
    )
    InterviewHistoryStore(history_path).load()

    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=history_path,
        school_options=["Palmdale"],
    )

    scored = model.flows["behavior_support_specialist"].items[0]
    assert scored.followups == ["What did you try first?", "What changed after that?"]
    assert scored.score_cards[0].description == "Serious concern. Unsafe or blaming."

def test_pyside_model_loads_legacy_history_into_history_rows(tmp_path: Path) -> None:
    canonical_dir = tmp_path / "user_artifacts"
    canonical_dir.mkdir()
    legacy_path = tmp_path / "interview_history.json"
    legacy_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "legacy-1",
                    "candidate_name": "Legacy Candidate",
                    "school": "Palmdale",
                    "position": "Preschool Teacher",
                    "offer_status": "not_generated",
                }
            ]
        ),
        encoding="utf-8",
    )

    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=canonical_dir / "interview_history.json",
        school_options=["Palmdale"],
    )

    assert [row.candidate for row in model.home.history_rows] == ["Legacy Candidate"]
    assert model.home.recent_interviews[0].candidate == "Legacy Candidate"

def test_pyside_history_rows_expose_school_position_and_offer_action(tmp_path: Path) -> None:
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "percent_of_max": 88.5,
            "outcome": "Hire",
            "offer_status": "not_generated",
            "interview_notes_path": str(tmp_path / "notes.docx"),
        }
    )

    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )

    row = model.home.history_rows[0]

    assert row.row_key == "hist-1"
    assert row.school == "Palmdale"
    assert row.position == "Preschool Teacher"
    assert row.offer_status == "not_generated"
    assert row.offer_action == "Generate Offer"
    assert row.interview_date == ""
    assert row.notes_path.endswith("notes.docx")

def test_pyside_history_rows_expose_deepseek_processing_state(tmp_path: Path) -> None:
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "processing",
            "deepseek_processing_warning": "Queued for local DeepSeek.",
        }
    )

    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )

    row = model.home.history_rows[0]

    assert row.deepseek_processing_status == "processing"
    assert row.deepseek_processing_warning == "Queued for local DeepSeek."

def test_pyside_history_filters_read_latest_rows_from_sqlite(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "outcome": "Hire",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    store.append(
        {
            "history_id": "hist-2",
            "candidate_name": "Fresh Candidate",
            "school": "Palmdale",
            "position": "Lead Teacher",
            "outcome": "Hire",
        }
    )

    window._set_history_school_filter("Palmdale")
    app.processEvents()

    visible_candidates = [
        window.history_table.item(row, 1).text()
        for row in range(window.history_table.rowCount())
    ]
    assert visible_candidates == ["Fresh Candidate", "Latoya Nugent"]
    window.window.close()
    app.processEvents()

def test_pyside_history_rows_show_newest_interviews_first(tmp_path: Path) -> None:
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-old",
            "candidate_name": "Old Candidate",
            "interview_date": "2026-07-07",
        }
    )
    store.append(
        {
            "history_id": "hist-today",
            "candidate_name": "Today Candidate",
            "interview_date": "2026-07-08",
        }
    )

    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )

    assert [row.candidate for row in model.home.history_rows] == ["Today Candidate", "Old Candidate"]
    assert model.home.recent_interviews[0].candidate == "Today Candidate"

def test_pyside_history_grid_shows_date_and_open_notes_action(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx placeholder", encoding="utf-8")
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "interview_date": "2026-06-23",
            "offer_status": "not_generated",
            "interview_notes_path": str(notes_path),
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    assert window.history_table.columnCount() == 11
    assert window.history_table.horizontalHeaderItem(0).text() == "Date"
    assert window.history_table.horizontalHeaderItem(6).text() == "Notes"
    assert window.history_table.horizontalHeaderItem(7).text() == "Regenerate"
    assert window.history_table.horizontalHeaderItem(8).text() == "Transcript"
    assert window.history_table.horizontalHeaderItem(10).text() == "Delete"
    assert window.history_table.item(0, 0).text() == "2026-06-23"
    notes_button = window.history_table.cellWidget(0, 6)
    assert notes_button.text() == "Open Notes"
    assert notes_button.property("history_row_key") == "hist-1"
    assert notes_button.isEnabled()
    regenerate_button = window.history_table.cellWidget(0, 7)
    assert regenerate_button.text() == "Regenerate"
    assert regenerate_button.property("history_row_key") == "hist-1"
    assert regenerate_button.isEnabled()
    transcript_button = window.history_table.cellWidget(0, 8)
    assert transcript_button.text() == "Import"
    assert transcript_button.property("history_row_key") == "hist-1"
    assert transcript_button.isEnabled()
    delete_button = window.history_table.cellWidget(0, 10)
    assert delete_button.text() == "Delete"
    assert delete_button.property("history_row_key") == "hist-1"
    assert delete_button.isEnabled()
    window.window.close()
    app.processEvents()

def test_pyside_history_grid_shows_processing_until_deepseek_finishes(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "processing",
            "interview_notes_path": str(tmp_path / "notes.docx"),
        }
    )
    store.append(
        {
            "history_id": "hist-2",
            "candidate_name": "Dalia Gaspar",
            "deepseek_processing_status": "failed",
            "deepseek_processing_warning": "DeepSeek processing failed.",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    failed_button = window.history_table.cellWidget(0, 6)
    processing_button = window.history_table.cellWidget(1, 6)

    assert processing_button.text() == "Processing"
    assert not processing_button.isEnabled()
    assert processing_button.toolTip() == "DeepSeek is still processing interview notes."
    assert failed_button.text() == "Failed/Retry"
    assert failed_button.isEnabled()
    assert failed_button.toolTip() == "DeepSeek processing failed."
    window.window.close()
    app.processEvents()

def test_pyside_history_grid_filters_fuzzy_search_school_outcome_and_colors_rows(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "outcome": "Hire",
        }
    )
    store.append(
        {
            "history_id": "hist-2",
            "candidate_name": "Dalia Gaspar",
            "school": "Hawthorne",
            "position": "Preschool Teacher",
            "outcome": "No Hire",
        }
    )
    store.append(
        {
            "history_id": "hist-3",
            "candidate_name": "Mina Patel",
            "school": "Palmdale",
            "position": "Behavior Support Specialist",
            "outcome": "Needs Follow-up",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale", "Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    assert window.history_school_filter.itemText(0) == "All schools"
    assert [window.history_table.item(row, 1).text() for row in range(window.history_table.rowCount())] == [
        "Mina Patel",
        "Dalia Gaspar",
        "Latoya Nugent",
    ]
    hire_brush = window.history_table.item(2, 5).data(qt_core.Qt.ItemDataRole.BackgroundRole)
    no_hire_brush = window.history_table.item(1, 5).data(qt_core.Qt.ItemDataRole.BackgroundRole)
    assert hire_brush.color().name() != no_hire_brush.color().name()

    window.history_search_input.setText("Latoya Nujent")
    app.processEvents()

    assert window.history_table.rowCount() == 1
    assert window.history_table.item(0, 1).text() == "Latoya Nugent"

    window.history_search_input.clear()
    window.history_school_filter.setCurrentText("Palmdale")
    window.history_outcome_filter.setCurrentText("Needs Follow-up")
    app.processEvents()

    assert window.history_table.rowCount() == 1
    assert window.history_table.item(0, 1).text() == "Mina Patel"
    window.window.close()
    app.processEvents()

def test_pyside_history_grid_sorts_by_column(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append({"history_id": "hist-1", "candidate_name": "Zara Lee", "school": "Palmdale", "outcome": "Hire"})
    store.append({"history_id": "hist-2", "candidate_name": "Ana Cruz", "school": "Hawthorne", "outcome": "No Hire"})
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale", "Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    assert window.history_table.isSortingEnabled()

    window.history_table.sortItems(1, qt_core.Qt.SortOrder.AscendingOrder)
    app.processEvents()

    assert [window.history_table.item(row, 1).text() for row in range(window.history_table.rowCount())] == [
        "Ana Cruz",
        "Zara Lee",
    ]
    window.window.close()
    app.processEvents()

def test_pyside_history_grid_sizes_text_columns_and_keeps_action_buttons_compact(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Alexandria Montgomery",
            "school": "West Palmdale Learning Center",
            "position": "Behavior Support Specialist",
            "interview_date": "2026-06-24",
            "percent_of_max": "88.5",
            "outcome": "Needs Follow-up",
            "offer_status": "not_generated",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["West Palmdale Learning Center"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    table = window.history_table

    for column in range(0, 6):
        assert table.columnWidth(column) >= table.sizeHintForColumn(column)

    assert table.horizontalHeaderItem(6).text() == "Notes"
    assert table.columnWidth(6) <= 105
    assert table.columnWidth(7) <= 125
    assert table.columnWidth(8) <= 115
    assert table.cellWidget(0, 6).maximumWidth() <= 95
    assert table.cellWidget(0, 7).maximumWidth() <= 115
    assert table.cellWidget(0, 8).maximumWidth() <= 105
    window.window.close()
    app.processEvents()

def test_pyside_session_autosaves_and_resumes_guided_interview(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    draft_path = tmp_path / "drafts" / "latoya-preschool.json"

    session = PySideInterviewSession(model=model, draft_path=draft_path)
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    first = session.active_question()

    session.save_answer_and_advance(notes="Wants values-aligned school.", score="")

    assert draft_path.exists()
    assert first is not None
    assert session.current_index == 1

    resumed = PySideInterviewSession.load(model=model, draft_path=draft_path)

    assert resumed.candidate_name == "Latoya Nugent"
    assert resumed.school == "Palmdale"
    assert resumed.track_key == "preschool"
    assert resumed.current_index == 1
    assert resumed.answers[first.question_id]["notes"] == "Wants values-aligned school."
    assert resumed.active_question().kind == "custom"

def test_pyside_session_back_and_skip_preserve_answers_and_move_through_flow(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")

    intro = session.active_question()
    session.save_answer_and_advance(notes="Intro complete.")
    custom = session.active_question()
    session.save_answer_and_advance(notes="Values aligned.")
    scored = session.active_question()

    session.go_back()

    assert session.active_question() == custom
    assert session.answers[custom.question_id]["notes"] == "Values aligned."

    session.skip_active_question(notes="No extra custom notes.")
    assert session.active_question() == scored

    session.skip_active_question(notes="Skipped rating after discussion.")
    assert session.active_question() is None
    assert session.answers[scored.question_id]["score"] == ""
    assert session.answers[scored.question_id]["skipped"] is True
    assert intro is not None
    assert session.answers[intro.question_id]["notes"] == "Intro complete."

def test_pyside_live_footer_blocks_unrated_scored_next_and_keeps_skip_enabled(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro complete.")
    session.save_answer_and_advance(notes="Values aligned.")
    window.session = session
    window.session_track_key = session.track_key
    window.session_index = session.current_index
    window._render_live_question_page()

    footer_buttons = {
        button.property("pyside_live_footer_action"): button
        for button in window.interview_tabs.widget(2).findChildren(qt_widgets.QPushButton)
        if button.property("pyside_live_footer_action")
    }

    assert footer_buttons["back"].isEnabled()
    assert footer_buttons["skip"].isEnabled()
    assert not footer_buttons["finalize"].isEnabled()

    window.score_group.buttons()[0].setChecked(True)
    app.processEvents()

    assert footer_buttons["finalize"].isEnabled()
    window.window.close()
    app.processEvents()

def test_pyside_home_import_indeed_transcript_opens_rating_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    transcript_path = tmp_path / "indeed.txt"
    transcript_path.write_text(
        """
Speaker 1: Tell me about a time a child was having a hard moment emotionally.

Speaker 0: I noticed the child was upset and helped them name the feeling.
""",
        encoding="utf-8",
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.home_candidate_input.setText("Miriam")
    monkeypatch.setattr(
        window.QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(transcript_path), "Text files (*.txt)"),
    )
    window._start_pyside_interview_recording = lambda: (_ for _ in ()).throw(AssertionError("recording should not start"))

    window._import_indeed_transcript_from_home()

    assert window.session is not None
    assert window.session.active_question().question_id == "trait_1"
    assert "helped them name" in window.live_notes.toPlainText()
    assert not window.live_next_button.isEnabled()
    assert window.interview_tabs.currentIndex() == 2
    window.window.close()
    app.processEvents()

def test_pyside_history_import_indeed_transcript_opens_review_for_existing_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_BASE_DIR", tmp_path)
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-grace",
            "candidate_name": "Grace Morales",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "interview_date": "2026-07-10",
            "outcome": "Incomplete",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    transcript_path = tmp_path / "grace-indeed.txt"
    transcript_path.write_text(
        """
Speaker 0: Tell me about a time a child was having a hard moment emotionally.

Speaker 1: I got low, named the feeling, and helped the child find words.
""",
        encoding="utf-8",
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(
        window.QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(transcript_path), "Text files (*.txt)"),
    )
    row = window.model.home.history_rows[0]

    window._import_indeed_transcript_for_history_row(row)

    assert window.session is not None
    assert window.session.candidate_name == "Grace Morales"
    assert window.session.school == "Palmdale"
    assert window.interview_tabs.currentIndex() == 3
    review_page = window.interview_tabs.widget(3)
    assert "I got low, named the feeling" in _table_text(
        review_page.findChild(qt_widgets.QTableWidget, "PySideReviewQuestionTable"),
    )
    rating = review_page.findChild(qt_widgets.QSpinBox, "PySideReviewRating_trait_1")
    assert rating is not None
    rating.setValue(4)
    app.processEvents()
    window._apply_review_score_updates()

    stored = InterviewHistoryStore(history_path).load()[0]
    assert stored["review_scores"]["trait_1"] == "4"
    assert stored["imported_indeed_transcript"]["mapped_count"] == 1
    assert stored["answers"]["trait_1"]["notes"].startswith("I got low")
    assert stored["flow_recordings"][0]["candidate_transcript"].startswith("I got low")
    window.window.close()
    app.processEvents()

def test_pyside_history_import_regenerates_basic_notes_and_deepseek_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_BASE_DIR", tmp_path)
    history_path = tmp_path / "interview_history.sqlite3"
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    stale_notes = notes_dir / "2026-07-10 - Palmdale - Grace Morales - Basic Interview Notes.docx"
    stale_notes.write_text("stale", encoding="utf-8")
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-grace",
            "candidate_name": "Grace Morales",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "interview_date": "2026-07-10",
            "interview_notes_path": str(stale_notes),
            "saved_report_path": str(stale_notes),
            "deepseek_processing_status": "not_started",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    transcript_path = tmp_path / "grace-indeed.txt"
    transcript_path.write_text(
        """
Speaker 0: Tell me about a time a child was having a hard moment emotionally.

Speaker 1: I got low, named the feeling, and helped the child find words.
""",
        encoding="utf-8",
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(
        window.QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(transcript_path), "Text files (*.txt)"),
    )

    window._import_indeed_transcript_for_history_row(window.model.home.history_rows[0])

    row = InterviewHistoryStore(history_path).load()[0]
    notes_path = Path(row["interview_notes_path"])
    assert notes_path.exists()
    assert notes_path == stale_notes
    assert "Structured Behavioral Interview Notes" in _docx_text(notes_path)
    assert "I got low, named the feeling" in _docx_text(notes_path)
    job_path = Path(row["deepseek_job_path"])
    assert job_path.exists()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["history_id"] == "hist-grace"
    assert job["report_path"] == str(notes_path)
    assert job["payload"]["flow_transcript"][2]["candidate_transcript"].startswith("I got low")
    assert row["deepseek_processing_status"] == "not_started"
    window.window.close()
    app.processEvents()

def test_pyside_show_schedules_recording_interface_preload_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    scheduled = []

    def _single_shot(_delay, callback):
        scheduled.append(callback)

    monkeypatch.setattr(window.QtCore.QTimer, "singleShot", _single_shot)
    monkeypatch.setattr(window.window, "showMaximized", lambda: None)

    window.show()
    window.show()

    assert window._preload_recording_interface_async in scheduled
    assert scheduled.count(window._preload_recording_interface_async) == 1
    window.window.close()
    app.processEvents()

def test_pyside_home_delete_saved_draft_requires_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    draft_path = tmp_path / "drafts" / "latoya.json"
    draft_path.parent.mkdir()
    draft_path.write_text('{"schema":"pyside_interview_draft.v1"}', encoding="utf-8")
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(pyside_interview_app, "latest_pyside_draft_path", lambda _drafts_dir=None: draft_path)
    window._refresh_home_draft_panel()
    no = window.QtWidgets.QMessageBox.StandardButton.No
    yes = window.QtWidgets.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: no)
    window._delete_latest_draft()
    assert draft_path.exists()

    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: yes)
    window._delete_latest_draft()
    assert not draft_path.exists()
    assert not window.home_continue_button.isEnabled()
    assert not window.home_delete_draft_button.isEnabled()
    window.window.close()
    app.processEvents()

def test_pyside_home_draft_actions_share_start_panel_to_prioritize_history(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    buttons = {button.text(): button for button in window.interview_tabs.widget(0).findChildren(qt_widgets.QPushButton)}
    section_titles = [
        label.text()
        for label in window.interview_tabs.widget(0).findChildren(qt_widgets.QLabel, "SectionTitle")
    ]

    assert buttons["Continue"].parent() == buttons["Begin Interview"].parent()
    assert buttons["Delete Saved Draft"].parent() == buttons["Begin Interview"].parent()
    assert "Continue Draft" not in section_titles
    assert window.history_table.sizePolicy().verticalStretch() >= 1
    window.window.close()
    app.processEvents()

def test_pyside_back_reentry_overwrites_existing_flow_timestamp(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    window.session = session

    window._mark_flow_timestamp_at(1, 12.0)
    window._mark_flow_timestamp_at(1, 22.0, overwrite=True)

    assert session.flow_time_marks == [{"flow_index": 1, "t": 22.0}]
    window.window.close()
    app.processEvents()

def test_pyside_back_reentry_overwrites_existing_flow_end_timestamp(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    window.session = session
    window.recording_started_monotonic = 100.0
    session.flow_time_marks = [{"flow_index": 1, "t": 2.0, "end_t": 5.0}]
    window._overwrite_next_live_boundary_timestamp = True
    monkeypatch.setattr(pyside_interview_app.time, "monotonic", lambda: 112.0)

    assert window._close_flow_timestamp(1) == 12.0
    assert session.flow_time_marks == [{"flow_index": 1, "t": 2.0, "end_t": 12.0}]
    window.window.close()
    app.processEvents()

def test_pyside_back_reentry_next_overwrites_following_question_start(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    monkeypatch.setattr(pyside_interview_app.time, "monotonic", lambda: 112.0)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.current_index = 1
    session.flow_time_marks = [
        {"flow_index": 1, "t": 2.0, "end_t": 5.0},
        {"flow_index": 2, "t": 5.0},
    ]
    window.session = session
    window.recording_started_monotonic = 100.0
    window._overwrite_next_live_boundary_timestamp = True

    window._save_and_next()

    assert session.flow_time_marks == [
        {"flow_index": 1, "t": 2.0, "end_t": 12.0},
        {"flow_index": 2, "t": 12.0},
    ]
    window.window.close()
    app.processEvents()

def test_pyside_session_enforces_intro_custom_scored_final_custom_workflow(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_workflow_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")

    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")

    seen: list[tuple[str, str, str]] = []
    while (active := session.active_question()) is not None:
        seen.append((active.kind, active.question_id, active.progress_label))
        session.save_answer_and_advance(notes=f"notes for {active.question_id}", score="5" if active.kind == "trait" else "")

    assert session.candidate_name == "Latoya Nugent"
    assert session.school == "Palmdale"
    assert session.track_key == "preschool"
    assert seen == [
        ("intro", "intro_script", "Question 1 of 8"),
        ("qualification", "Why-ECE", "Question 2 of 8"),
        ("custom", "Why-LPL", "Question 3 of 8"),
        ("trait", "trait_1", "Question 4 of 8"),
        ("custom", "FT-or-PT", "Question 5 of 8"),
        ("custom", "Not-Avail", "Question 6 of 8"),
        ("custom", "Pay", "Question 7 of 8"),
        ("custom", "Start", "Question 8 of 8"),
    ]
    assert "Palmdale is open weekdays" in session.answers["intro_script"]["prompt"]

def test_pyside_recording_starts_on_begin_for_intro_audio_check(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    starts: list[str] = []
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: starts.append("start"))

    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()

    assert starts == ["start"]
    assert window.session.active_question().kind == "intro"

    window.live_notes.setPlainText("Intro read by interviewer.")
    window._save_and_next()

    assert starts == ["start"]
    assert window.session.active_question().kind == "custom"
    window.window.close()
    app.processEvents()

def test_pyside_recording_start_failure_shows_audio_device_warning(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(pyside_interview_app.sys, "platform", "win32")
    monkeypatch.setattr(pyside_interview_app, "resolve_default_windows_system_device", lambda: "Wrong Output Device")

    import interview_audio_recorder

    def _raise_bad_device(**_kwargs: object) -> object:
        raise RuntimeError("Recording process exited immediately. Check configured audio device names.")

    monkeypatch.setattr(interview_audio_recorder, "start_recording", _raise_bad_device)

    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()
    app.processEvents()

    warning = window.window.findChild(qt_widgets.QLabel, "PySideRecordingWarning")
    assert warning is not None
    assert "Wrong Output Device" in warning.text()
    assert "Check Windows/meeting output" in warning.text()
    assert window.recording_session is None
    assert window.recording_started_monotonic is None
    assert window.recording_system_device == "Wrong Output Device"
    window.window.close()
    app.processEvents()

def test_pyside_intro_audio_check_warns_on_blank_transcription(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()

    window._apply_pyside_intro_audio_check_result("")
    app.processEvents()

    warning = window.window.findChild(qt_widgets.QLabel, "PySideRecordingWarning")
    assert warning is not None
    assert "No speech was transcribed from the first 15 seconds" in warning.text()
    assert "Record the interview in Zoom" in warning.text()
    window.window.close()
    app.processEvents()

def test_pyside_intro_audio_check_ignores_interviewer_only_segments(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()

    session = SimpleNamespace(
        transcribe_new_segments=lambda **_kwargs: [SimpleNamespace(speaker="INTERVIEWER", text="Did you see our website?")]
    )

    assert window._transcribe_pyside_intro_audio_sample(session) == ""
    window.window.close()
    app.processEvents()

def test_pyside_system_audio_route_check_warns_when_capture_is_silent(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()
    window.live_notes.setPlainText("Intro read.")
    window.session.save_answer_and_advance(notes="Intro read.")
    window.session_index = window.session.current_index
    window._render_live_question_page()

    sys_wav = tmp_path / "silent_sys.wav"
    with wave.open(str(sys_wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 16000)

    window.recording_session = SimpleNamespace(sys_wav=sys_wav)
    window.recording_system_device = "VB-Audio Virtual Cable (CABLE Input)"
    window._check_pyside_system_audio_capture()
    app.processEvents()

    warning = window.window.findChild(qt_widgets.QLabel, "PySideRecordingWarning")
    assert warning is not None
    assert "No meeting/system audio detected yet" in warning.text()
    assert "switch Zoom/Windows output to VB-CABLE" in warning.text()
    window.window.close()
    app.processEvents()

def test_pyside_qualification_screen_keeps_education_and_experience_with_why_ece(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_workflow_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    draft_path = tmp_path / "draft.json"
    session = PySideInterviewSession(model=model, draft_path=draft_path)
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro read.")

    qualification_screen = session.active_question()
    assert qualification_screen is not None
    assert qualification_screen.kind == "qualification"
    assert qualification_screen.question_id == "Why-ECE"
    assert "why early childhood education" in qualification_screen.prompt.lower()

    session.save_answer_and_advance(
        notes="Grew up helping younger siblings.",
        qualification={
            "has_degree": True,
            "degree_type": "BA",
            "degree_in_ece": False,
            "ece_units_completed": 18,
            "infant_toddler_class_completed": True,
            "total_units_completed": None,
            "years_experience": 4,
        },
    )

    resumed = PySideInterviewSession.load(model=model, draft_path=draft_path)
    answer = resumed.answers["Why-ECE"]

    assert answer["notes"] == "Grew up helping younger siblings."
    assert answer["qualification"]["degree_type"] == "BA"
    assert answer["qualification"]["ece_units_completed"] == 18
    assert answer["qualification"]["years_experience"] == 4

def test_pyside_finalize_payload_uses_qualification_fields_and_keeps_why_ece_transcript(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_BASE_DIR", tmp_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_workflow_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Lisvelia Pazos-Hilario", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro read.")
    session.save_answer_and_advance(
        notes="",
        qualification={
            "has_degree": True,
            "degree_type": "AA",
            "degree_in_ece": True,
            "ece_units_completed": None,
            "infant_toddler_class_completed": False,
            "total_units_completed": None,
            "years_experience": 7,
        },
    )
    session.qualification = {}
    session.flow_candidate_transcripts[1] = "Why-ECE candidate answer from transcript."
    session.save_answer_and_advance(notes="Why LPL manual note.")
    session.save_answer_and_advance(notes="Scored example.", score="5")
    session.save_answer_and_advance(notes="full-time")
    session.flow_candidate_transcripts[4] = "Noisy ASR for FT-or-PT should not win."
    session.save_answer_and_advance(notes="no")
    session.flow_candidate_transcripts[5] = "Noisy ASR for Not-Avail should not win."
    session.save_answer_and_advance(notes="Asked for 35/hour.")
    session.flow_candidate_transcripts[6] = "Noisy ASR for Pay should not win."
    session.save_answer_and_advance(notes="asap")
    session.flow_candidate_transcripts[7] = "Noisy ASR for Start should not win."
    adapter = pyside_interview_app._PySideFinalizeAdapter(
        session,
        base_dir=tmp_path,
        history_path=tmp_path / "history.sqlite3",
    )
    scoring = pyside_interview_app.ScoringEngine.evaluate(
        adapter._rubric_with_question_overrides(),
        adapter.state.track,
        adapter.state.trait_inputs,
    )

    context = pyside_interview_app.build_finalize_context(
        adapter,
        scoring,
        [],
        session._transcript_metadata(),
        run_deepseek=False,
    )

    assert context.payload["candidate"]["qualification"] == {
        "has_degree": True,
        "degree_type": "AA",
        "degree_in_ece": True,
        "ece_units_completed": None,
        "infant_toddler_class_completed": False,
        "total_units_completed": None,
        "years_experience": 7,
    }
    by_id = {item["id"]: item for item in context.payload["flow_transcript"]}
    assert by_id["Why-ECE"]["candidate_transcript"] == "Why-ECE candidate answer from transcript."
    by_id = {item["id"]: item for item in context.payload["flow_transcript"]}
    assert by_id["FT-or-PT"]["candidate_transcript"] == "full-time"
    assert by_id["Not-Avail"]["candidate_transcript"] == "no"
    assert by_id["Pay"]["candidate_transcript"] == "Asked for 35/hour."
    assert by_id["Start"]["candidate_transcript"] == "asap"

def test_latest_pyside_draft_path_returns_newest_json(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")

    assert latest_pyside_draft_path(tmp_path) == new

def test_pyside_session_review_summary_uses_scoring_rules(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")

    summary = session.review_summary()

    assert summary.percent_of_max == 100.0
    assert summary.outcome == "Hire"
    assert summary.next_action == "Generate Offer"
    assert summary.missing_scores == []
    assert summary.strongest_evidence == ["Warm child-centered example."]

def test_pyside_session_review_score_update_recalculates_and_persists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_BASE_DIR", tmp_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="")
    session.flow_candidate_transcripts[2] = "I helped a child breathe and name their feelings."

    session.update_review_score("trait_1", 5)

    assert session.answers["trait_1"]["score"] == "5"
    assert session.review_summary().outcome == "Hire"
    draft = json.loads((tmp_path / "draft.json").read_text(encoding="utf-8"))
    assert draft["answers"]["trait_1"]["score"] == "5"
    session_file = next((tmp_path / "interview_sessions").glob("*.json"))
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["questions"]["2"]["notes"]["raw_score"] == 5
    assert payload["questions"]["2"]["candidate_transcript"] == "I helped a child breathe and name their feelings."

def test_pyside_offer_review_defaults_are_prefilled_from_completed_session(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")

    defaults = session.offer_review_defaults()

    assert defaults["candidate"] == "Latoya Nugent"
    assert defaults["school"] == "Palmdale"
    assert defaults["position"] == "Preschool"
    assert defaults["determination"] == "Hire"
    assert defaults["next_action"] == "Generate Offer"

def test_pyside_review_screen_hides_finalize_and_offer_actions(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")
    window = _pyside_window_on_page(model, "Offers")
    window.session = session
    window._render_review_page()

    review_button_text = [
        child.text()
        for child in window.interview_tabs.widget(3).findChildren(qt_widgets.QPushButton)
    ]
    assert "Finalize Interview" not in review_button_text
    assert "Generate Offer" not in review_button_text

    offer_buttons = [
        child
        for child in window.stack.widget(2).findChildren(qt_widgets.QPushButton)
        if child.text() == "Generate Offer"
    ]
    assert offer_buttons
    window.window.close()
    app.processEvents()

def test_pyside_review_screen_shows_interviewer_closeout_without_slow_outputs(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro complete.", score="")
    session.skip_active_question(notes="No extra qualification notes.")
    session.save_answer_and_advance(notes="Candidate transcript should stay hidden.", score="")
    session.flow_candidate_transcripts[2] = "Transcript text should not render."
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session

    window._render_review_page()

    review_page = window.interview_tabs.widget(3)
    visible_text = _widget_text(review_page)
    assert "Interviewer Closeout" in visible_text
    assert "Latoya Nugent" in visible_text
    assert "Palmdale" in visible_text
    assert "Interview saved" in visible_text
    assert "Report files are being prepared in the background" in visible_text
    assert "Manual Score:" in visible_text
    assert "Determination:" in visible_text
    assert "Next Step:" in visible_text
    assert "Captured 3 of 3 configured interview responses." in visible_text
    assert "Send candidate to director interview if required by your hiring workflow." in visible_text
    assert "DeepSeek" not in visible_text
    assert "AI" not in visible_text
    assert "Candidate transcript should stay hidden." not in visible_text
    assert "Transcript text should not render." not in visible_text

    needs = review_page.findChild(qt_widgets.QListWidget, "PySideReviewNeedsList")
    assert needs is not None
    assert [needs.item(row).text() for row in range(needs.count())] == [
        "Missing score: Empathy",
        "Skipped: Non-scored question",
    ]

    table = review_page.findChild(qt_widgets.QTableWidget, "PySideReviewQuestionTable")
    assert table is not None
    assert table.columnCount() == 5
    assert [table.horizontalHeaderItem(column).text() for column in range(5)] == [
        "Question",
        "Score",
        "Notes",
        "Transcript",
        "Flags",
    ]
    assert table.item(2, 0).text() == "Empathy"
    rating = table.cellWidget(2, 1)
    assert isinstance(rating, qt_widgets.QSpinBox)
    assert rating.value() == 0
    assert table.item(2, 2).text() == "Yes"
    assert table.item(2, 3).text() == "Transcript text should not render."
    window.window.close()
    app.processEvents()

def test_pyside_review_screen_allows_rating_change_from_transcript(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_BASE_DIR", tmp_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro complete.", score="")
    session.save_answer_and_advance(notes="No extra qualification notes.", score="")
    session.save_answer_and_advance(notes="Review this against transcript.", score="")
    session.flow_candidate_transcripts[2] = "Candidate described calming a child and helping them use words."
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = session

    window._render_review_page()
    review_page = window.interview_tabs.widget(3)
    rating = review_page.findChild(qt_widgets.QSpinBox, "PySideReviewRating_trait_1")
    assert rating is not None
    assert rating.value() == 0
    assert "Candidate described calming a child" in _table_text(
        review_page.findChild(qt_widgets.QTableWidget, "PySideReviewQuestionTable"),
    )

    rating.setValue(5)
    app.processEvents()

    assert session.answers["trait_1"]["score"] == "5"
    assert "Determination: Hire" in _widget_text(review_page)
    needs = review_page.findChild(qt_widgets.QListWidget, "PySideReviewNeedsList")
    assert needs is not None
    assert "Missing score: Empathy" not in [needs.item(row).text() for row in range(needs.count())]
    window.window.close()
    app.processEvents()

def test_pyside_session_generates_offer_document_from_template(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")
    template_path = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("[First Name] [Last Name] | [City] | [Position] | [HourlyPay] | [Hours]")
    doc.save(template_path)

    output_path = session.generate_offer_document(
        template_path=template_path,
        output_dir=tmp_path / "offers",
        start_date=date(2026, 6, 23),
        start_time_12h="08:00 AM",
        end_time_12h="05:00 PM",
        hourly_pay=22.5,
        hours=40,
        created_on=date(2026, 6, 20),
    )

    assert output_path.exists()
    rendered = _docx_text(output_path)
    assert "Latoya Nugent | Palmdale | Preschool | 22.50 | 40" in rendered

def test_pyside_session_offer_generation_emits_start_date_notification(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")
    template_path = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("[First Name] [Last Name] | [City] | [Position] | [HourlyPay] | [Hours]")
    doc.save(template_path)
    notifications = []

    class FakeNotifications:
        def emit_event(self, event_type, payload, idempotency_key):
            notifications.append((event_type, payload, idempotency_key))
            return []

    window = _pyside_window_on_page(model, "Offers")
    window.session = session
    window.notification_service = FakeNotifications()
    window._open_session_offer()
    window.offer_fields["template_path"].setText(str(template_path))
    window.offer_fields["output_dir"].setText(str(tmp_path / "offers"))
    window.offer_fields["start_date"].setText("2026-07-10")
    window.offer_fields["hourly_pay"].setText("22.50")
    window.offer_fields["hours_week"].setText("40")

    window._generate_offer_from_fields()

    assert notifications[0][0] == "offer.generated"
    assert notifications[0][1]["candidate_name"] == "Latoya Nugent"
    assert notifications[0][1]["start_date"] == "2026-07-10"
    assert notifications[0][2].endswith(":offer.generated")
    window.window.close()
    app.processEvents()

def test_pyside_window_runs_due_notification_schedule_on_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    runs = []

    class FakeNotifications:
        def run_due_notifications(self):
            runs.append("ran")
            return []

    monkeypatch.setattr(
        pyside_interview_app,
        "notification_service_from_email_account_settings",
        lambda **_kwargs: FakeNotifications(),
    )

    window = pyside_interview_app.PySideInterviewWindow(model)
    window._schedule_startup_notifications()
    app.processEvents()

    assert runs == ["ran"]
    window.window.close()
    app.processEvents()

def test_pyside_session_generates_interview_notes_document(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")

    output_path = session.generate_interview_notes_document(output_dir=tmp_path / "notes")

    assert output_path.exists()
    rendered = _docx_text(output_path)
    assert "Latoya Nugent" in rendered
    assert "Warm child-centered example." in rendered
    assert "Final Outcome" in rendered
    assert "Hire" in rendered
    assert (tmp_path / "notes" / "interview_history.sqlite3").exists()
    assert not (tmp_path / "notes" / "interview_history.json").exists()

def test_pyside_finalize_writes_basic_notes_without_deepseek_queue(tmp_path: Path, monkeypatch) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")
    history_path = tmp_path / "interview_history.sqlite3"
    def _fail_enqueue(*_args, **_kwargs) -> Path:
        raise AssertionError("DeepSeek must only run from candidate history Generate")

    monkeypatch.setattr(pyside_interview_app, "enqueue_deepseek_finalize_job", _fail_enqueue)

    result = session.finalize_interview(base_dir=tmp_path, history_path=history_path)

    report_path = Path(result["out_path"])
    integration_path = Path(result["integration_path"])
    rows = InterviewHistoryStore(history_path).load()

    assert report_path.exists()
    assert integration_path.exists()
    assert "pyside_notes" not in str(report_path)
    rendered = _docx_text(report_path)
    assert "Structured Behavioral Interview Notes" in rendered
    assert "1. Candidate Snapshot" in rendered
    assert "2. Candidate Education and Experience Summary" in rendered
    assert "3. Score Summary" in rendered
    assert "4. Candidate Answers" in rendered
    assert "Warm child-centered example." in rendered
    assert "Weighted Total" in rendered
    assert "Final Outcome" in rendered
    assert "Consolidated Answer Summaries" not in rendered
    assert rows[0]["candidate_name"] == "Latoya Nugent"
    assert Path(rows[0]["interview_notes_path"]) == report_path
    assert rows[0]["deepseek_processing_status"] == "not_started"
    assert result["deepseek_job_path"] == ""
    assert result["deepseek_progress_path"] == ""

def test_pyside_finalize_writes_interview_notes_to_school_dropbox_folder(tmp_path: Path, monkeypatch) -> None:
    dropbox_root = tmp_path / "Dropbox"
    base_dir = dropbox_root / "App" / "interviews"
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Palmdale": {
                    "interview_notes_dir": r"\Dropbox\LPL PMD Office Shared\Staff\Candidates",
                }
            }
        ),
        encoding="utf-8",
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Mission aligned.", score="")
    session.save_answer_and_advance(notes="Values aligned.", score="")
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "enqueue_deepseek_finalize_job", lambda *_args: Path())

    result = session.finalize_interview(base_dir=base_dir, history_path=tmp_path / "interview_history.sqlite3")

    report_path = Path(result["out_path"])
    assert report_path.parent == dropbox_root / "LPL PMD Office Shared" / "Staff" / "Candidates"
    assert report_path.exists()

def test_pyside_admin_school_folder_settings_review_confirm_persists(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps({"Palmdale": {"offer_output_dir": "offers", "interview_notes_dir": "old"}}),
        encoding="utf-8",
    )
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:14b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )

    window = _pyside_window_on_page(model, "Admin")
    table = window.school_folder_settings_table
    assert table.editTriggers() == qt_widgets.QAbstractItemView.EditTrigger.NoEditTriggers
    window.admin_edit_button.click()
    assert table.editTriggers() != qt_widgets.QAbstractItemView.EditTrigger.NoEditTriggers
    palmdale_row = next(
        row for row in range(table.rowCount()) if table.item(row, 0).text() == "Palmdale"
    )
    table.item(palmdale_row, 1).setText(r"\Dropbox\LPL PMD Office Shared\Staff\Candidates")

    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: window.QtWidgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window.QtWidgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window.QtWidgets.QMessageBox, "information", lambda *_args, **_kwargs: None)
    window._review_admin_changes()

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["Palmdale"]["offer_output_dir"] == "offers"
    assert saved["Palmdale"]["interview_notes_dir"] == r"\Dropbox\LPL PMD Office Shared\Staff\Candidates"
    window.window.close()

def test_pyside_finalize_preserves_transcribed_audio_in_basic_notes(tmp_path: Path, monkeypatch) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro notes.", score="")
    session.save_answer_and_advance(notes="Custom notes.", score="")
    session.save_answer_and_advance(notes="Manual fallback notes.", score="5")
    session.flow_recordings = {
        index: {
            "flow_index": index,
            "base_name": "pyside-recording",
            "transcript_jsonl": str(tmp_path / "transcript.jsonl"),
            "candidate_transcript": transcript,
        }
        for index, transcript in {
            0: "Candidate heard the intro.",
            1: "Candidate gave custom answer.",
            2: "Candidate described a warm child-centered example.",
        }.items()
    }
    session.flow_candidate_transcripts = {
        0: "Candidate heard the intro.",
        1: "Candidate gave custom answer.",
        2: "Candidate described a warm child-centered example.",
    }
    result = session.finalize_interview(base_dir=tmp_path, history_path=tmp_path / "interview_history.sqlite3")

    assert result["transcript_complete"] is True
    rendered = _docx_text(Path(result["out_path"]))
    assert "Candidate heard the intro." in rendered
    assert "Candidate gave custom answer." in rendered
    assert "Candidate described a warm child-centered example." in rendered
    rows = InterviewHistoryStore(tmp_path / "interview_history.sqlite3").load()
    assert rows[0]["flow_recordings"] == list(session.flow_recordings.values())
    assert rows[0]["deepseek_processing_status"] == "not_started"

def test_pyside_last_question_footer_finalizes_and_shows_complete_home(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    finalized: list[bool] = []

    def _fake_generate() -> None:
        finalized.append(True)
        window.review_status_label.setText("Interview finalized: fake.docx")

    monkeypatch.setattr(window, "_generate_interview_notes_from_session", _fake_generate)
    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()

    while window.session is not None and window.session.active_question() is not None:
        question = window.session.active_question()
        buttons = {
            button.text(): button
            for button in window.window.findChildren(qt_widgets.QPushButton)
            if button.property("pyside_live_footer_action")
        }
        assert "Exit" in buttons
        if question == window.session._workflow_items()[-1]:
            assert "Finalize" in buttons
            if question.score_cards:
                window.score_group.buttons()[0].setChecked(True)
            buttons["Finalize"].click()
            break
        assert "Next" in buttons
        window.live_notes.setPlainText(f"notes for {question.question_id}")
        if question.score_cards:
            window.score_group.buttons()[0].setChecked(True)
        buttons["Next"].click()

    app.processEvents()

    assert finalized == [True]
    assert window.interview_tabs.currentIndex() == 3
    assert "Interviewer Closeout" in window.interview_tabs.currentWidget().findChild(qt_widgets.QLabel, "Title").text()
    assert any(button.text() == "Home" for button in window.window.findChildren(qt_widgets.QPushButton))
    window.window.close()
    app.processEvents()

def test_pyside_next_marks_new_question_at_click_boundary(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    ticks = iter([100.0, 105.0, 109.0])
    monkeypatch.setattr(pyside_interview_app.time, "monotonic", lambda: next(ticks))

    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()
    window.recording_started_monotonic = 100.0
    window._render_live_question_page()
    window.live_notes.setPlainText("Intro read.")
    window._save_and_next()

    marks = window.session.flow_time_marks
    assert marks[0]["flow_index"] == 0
    assert marks[0]["end_t"] == 5.0
    assert marks[1]["flow_index"] == 1
    assert marks[1]["t"] == 5.0
    window.window.close()
    app.processEvents()

def test_pyside_review_table_shows_generated_transcripts(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    session.save_answer_and_advance(notes="Intro notes.")
    session.save_answer_and_advance(notes="Manual custom notes.")
    session.save_answer_and_advance(notes="Manual scored notes.", score="5")
    session.flow_candidate_transcripts[1] = "Generated custom transcript."
    session.flow_candidate_transcripts[2] = "Generated scored transcript."
    window.session = session

    window._render_review_page()

    table = window.window.findChild(qt_widgets.QTableWidget, "PySideReviewQuestionTable")
    headers = [table.horizontalHeaderItem(index).text() for index in range(table.columnCount())]
    assert "Transcript" in headers
    transcript_column = headers.index("Transcript")
    transcript_values = [
        table.item(row, transcript_column).text()
        for row in range(table.rowCount())
        if table.item(row, transcript_column) is not None
    ]
    assert "Generated custom transcript." in transcript_values
    assert "Generated scored transcript." in transcript_values
    window.window.close()
    app.processEvents()

def test_pyside_keeps_recording_active_after_last_scored_question_navigation(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_workflow_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Interviews")
    session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    window.session = session
    window.recording_session = object()

    workflow = session._workflow_items()
    first_custom_index = next(index for index, item in enumerate(workflow) if item.kind == "custom")
    trait_index = next(index for index, item in enumerate(workflow) if item.kind == "trait")

    assert window._should_stop_recording_after_question(first_custom_index, workflow[first_custom_index]) is False
    assert window._should_stop_recording_after_question(trait_index, workflow[trait_index]) is False
    window.window.close()
    app.processEvents()

def test_pyside_last_scored_question_routes_to_review_before_transcription(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()

    while window.session is not None and window.session.active_question() is not None:
        question = window.session.active_question()
        window.live_notes.setPlainText(f"notes for {question.question_id}")
        if question.score_cards:
            window.score_group.buttons()[0].setChecked(True)
        if question == window.session._workflow_items()[-1]:
            break
        window._save_and_next()

    stopped: list[bool] = []

    class BlockingRecordingSession:
        def stop_and_transcribe(self, **_kwargs):
            stopped.append(True)
            raise AssertionError("recording stop must run from finalize worker")

    window.recording_session = BlockingRecordingSession()
    monkeypatch.setattr(window, "_generate_interview_notes_from_session", lambda: None)
    window._save_and_next(finalize=True)

    assert stopped == []
    assert window.interview_tabs.currentIndex() == 3
    assert "Interviewer Closeout" in window.interview_tabs.currentWidget().findChild(qt_widgets.QLabel, "Title").text()
    assert window.recording_session is not None
    window.window.close()
    app.processEvents()

def test_pyside_finalize_returns_while_recording_transcription_finishes(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    monkeypatch.setattr(window, "_start_pyside_interview_recording", lambda: None)
    window.home_candidate_input.setText("Latoya Nugent")
    window._begin_selected_interview()
    while window.session.active_question() is not None:
        window.session.save_answer_and_advance(
            notes="Evidence.",
            score="5" if window.session.current_index == 2 else "",
        )
    window._render_review_page()
    started = threading.Event()
    release = threading.Event()

    class SlowRecordingSession:
        def stop_and_transcribe(self, **_kwargs):
            started.set()
            release.wait(timeout=2)
            raise RuntimeError("synthetic transcription delay")

    window.recording_session = SlowRecordingSession()
    finalized: list[bool] = []
    scheduled_closes: list[bool] = []
    monkeypatch.setattr(window.session, "finalize_interview", lambda **_kwargs: finalized.append(True) or {"out_path": "done.docx"})
    monkeypatch.setattr(window, "_schedule_close_pyside_finalize_progress", lambda: scheduled_closes.append(True))

    window._generate_interview_notes_from_session()

    assert started.wait(timeout=1)
    assert finalized == []
    assert "Finalizing interview" in window.review_status_label.text()
    release.set()
    for _ in range(50):
        app.processEvents()
        if "Interview finalized:" in window.review_status_label.text():
            break

    assert finalized == [True]
    assert "Interview finalized: done.docx" in window.review_status_label.text()
    assert scheduled_closes == [True]
    window.window.close()
    app.processEvents()

def test_pyside_finalize_progress_window_is_user_closable_and_non_canceling(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window._show_pyside_finalize_progress("Stopping recording")

    progress_dialog = window.pyside_finalize_progress_dialog
    progress_label = window.pyside_finalize_progress_label

    assert progress_dialog is not None
    assert "Stopping recording" in progress_label.text()
    assert "Processing" in progress_label.text()
    assert progress_dialog.windowTitle() == "Finalizing Interview"

    progress_dialog.close()
    app.processEvents()

    assert window.pyside_finalize_progress_dialog is None
    assert window._pyside_finalize_running is False
    window._report_pyside_finalize_progress("Building interview notes")
    assert window._pyside_finalize_progress_step == "Building interview notes"
    window.window.close()
    app.processEvents()

def test_pyside_finalize_progress_scheduled_close_uses_one_shot_timer(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window._show_pyside_finalize_progress("Interview finalized")
    single_shots: list[int] = []

    class ImmediateTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            single_shots.append(delay_ms)
            callback()

    monkeypatch.setattr(window.QtCore, "QTimer", ImmediateTimer)

    window._schedule_close_pyside_finalize_progress()
    app.processEvents()

    assert single_shots == [2500]
    assert window.pyside_finalize_progress_dialog is None
    window.window.close()
    app.processEvents()

def test_pyside_progress_window_polls_deepseek_progress_json(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    progress_path = tmp_path / "deepseek.progress.json"
    progress_path.write_text(json.dumps({"step": "Updating interview notes document", "status": "processing"}), encoding="utf-8")

    window._show_pyside_finalize_progress("Queueing DeepSeek processing")
    window._watch_pyside_deepseek_finalize_progress(progress_path)
    app.processEvents()

    assert window.pyside_finalize_deepseek_progress_path == progress_path
    assert "Updating interview notes document" in window.pyside_finalize_progress_label.text()
    assert "Processing" in window.pyside_finalize_progress_label.text()

    progress_path.write_text(json.dumps({"step": "Summarizing Q3: Trait 1", "status": "processing"}), encoding="utf-8")
    window._refresh_pyside_finalize_progress()
    app.processEvents()

    assert "Summarizing Q3: Trait 1" in window.pyside_finalize_progress_label.text()

    progress_path.write_text(json.dumps({"step": "Complete", "status": "complete"}), encoding="utf-8")
    window._watch_pyside_deepseek_finalize_progress(progress_path)
    app.processEvents()

    assert "Complete" in window.pyside_finalize_progress_label.text()
    window._close_pyside_finalize_progress()
    window.window.close()
    app.processEvents()

def test_pyside_progress_window_auto_closes_after_deepseek_complete(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    progress_path = tmp_path / "deepseek.progress.json"
    scheduled_closes: list[bool] = []
    window._schedule_close_pyside_finalize_progress = lambda: scheduled_closes.append(True)

    window._show_pyside_finalize_progress("Queueing DeepSeek processing")
    progress_path.write_text(json.dumps({"step": "Complete", "status": "complete"}), encoding="utf-8")
    window._watch_pyside_deepseek_finalize_progress(progress_path)
    app.processEvents()

    assert scheduled_closes == [True]
    assert "Complete" in window.pyside_finalize_progress_label.text()

    scheduled_closes.clear()
    window._show_pyside_finalize_progress("Queueing DeepSeek processing")
    progress_path.write_text(json.dumps({"step": "DeepSeek failed", "status": "failed"}), encoding="utf-8")
    window._watch_pyside_deepseek_finalize_progress(progress_path)
    app.processEvents()

    assert scheduled_closes == []
    assert window.pyside_finalize_progress_dialog is not None
    window._close_pyside_finalize_progress()
    window.window.close()
    app.processEvents()

def test_pyside_progress_window_renders_task_status_list(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window._show_pyside_finalize_progress("Queueing DeepSeek processing")
    window._pyside_finalize_progress_tasks = [
        {"name": "Stopping recording", "status": "Finished"},
        {"name": "Queueing DeepSeek processing", "status": "Processing"},
        {"name": "Waiting for DeepSeek queue", "status": "Queued"},
    ]

    window._refresh_pyside_finalize_progress()
    app.processEvents()

    progress_text = window.pyside_finalize_progress_label.text()
    assert "Stopping recording" in progress_text
    assert "Finished" in progress_text
    assert "Queueing DeepSeek processing" in progress_text
    assert "Processing" in progress_text
    assert "Waiting for DeepSeek queue" in progress_text
    assert "Queued" in progress_text
    window._close_pyside_finalize_progress()
    window.window.close()
    app.processEvents()

def test_pyside_progress_window_discovers_deepseek_progress_from_history(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    jobs_dir = tmp_path / "deepseek_jobs"
    jobs_dir.mkdir()
    progress_path = jobs_dir / "deepseek-finalize-hist-1.progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "step": "Waiting for DeepSeek queue",
                "status": "processing",
                "tasks": [
                    {"name": "Launching local DeepSeek worker", "status": "Finished"},
                    {"name": "Waiting for DeepSeek queue", "status": "Processing"},
                ],
            }
        ),
        encoding="utf-8",
    )
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "processing",
            "deepseek_progress_path": str(progress_path),
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    window._show_pyside_finalize_progress("Queueing DeepSeek processing")
    window._refresh_pyside_finalize_progress()
    app.processEvents()

    progress_text = window.pyside_finalize_progress_label.text()
    assert window.pyside_finalize_deepseek_progress_path == progress_path
    assert "Waiting for DeepSeek queue" in progress_text
    assert "Processing" in progress_text
    window._close_pyside_finalize_progress()
    window.window.close()
    app.processEvents()

def test_pyside_progress_window_immediately_shows_ordered_tasks_in_scroll_area(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    window._show_pyside_finalize_progress("Queueing DeepSeek processing")
    app.processEvents()

    scroll_areas = window.pyside_finalize_progress_dialog.findChildren(qt_widgets.QScrollArea)
    progress_text = window.pyside_finalize_progress_label.text()

    assert scroll_areas
    assert window.pyside_finalize_progress_dialog.maximumHeight() <= 460
    assert progress_text.index("Launching local DeepSeek worker") < progress_text.index("Waiting for DeepSeek queue")
    assert progress_text.index("Analyzing traits") < progress_text.index("Scoring traits")
    assert "Updating interview notes document" in progress_text
    assert progress_text.rstrip().endswith("Queued")
    window._close_pyside_finalize_progress()
    window.window.close()
    app.processEvents()

def test_pyside_finalize_reload_history_after_queueing_deepseek(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).load()
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Candidates")
    window.session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    window.session.start(candidate_name="Latoya Nugent", school="Palmdale", track_key="preschool")
    monkeypatch.setattr(window, "_stop_pyside_interview_recording", lambda: None)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_HISTORY_PATH", history_path)

    def _fake_finalize(**_kwargs):
        InterviewHistoryStore(history_path).append(
            {
                "history_id": "hist-1",
                "candidate_name": "Latoya Nugent",
                "deepseek_processing_status": "processing",
            }
        )
        return {"out_path": str(tmp_path / "notes.docx"), "deepseek_progress_path": ""}

    monkeypatch.setattr(window.session, "finalize_interview", _fake_finalize)

    window._generate_interview_notes_from_session()
    for _ in range(50):
        app.processEvents()
        if window.history_table.rowCount() == 1:
            break

    assert window.history_table.rowCount() == 1
    assert window.history_table.item(0, 1).text() == "Latoya Nugent"
    assert window.history_table.cellWidget(0, 6).text() == "Processing"
    assert window.candidate_history_table.rowCount() == 1
    assert window.candidate_history_table.item(0, 1).text() == "Latoya Nugent"
    assert window.candidate_history_table.cellWidget(0, 6).text() == "Processing"
    window.window.close()
    app.processEvents()

def test_pyside_review_table_updates_transcript_when_finalize_worker_reports_transcripts(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    window.session.start(candidate_name="Live Transcript", school="Palmdale", track_key="preschool")
    for item in window.session._workflow_items():
        window.session.answers[item.question_id] = {
            "kind": item.kind,
            "title": item.title,
            "prompt": item.prompt,
            "notes": "Saved note",
            "score": "3" if item.kind == "trait" else "",
            "quick_actions": [],
        }
    window.session.current_index = len(window.session._workflow_items())
    window._render_review_page()
    table = window.window.findChild(qt_widgets.QTableWidget, "PySideReviewQuestionTable")
    trait_row = next(row for row in range(table.rowCount()) if table.cellWidget(row, 1) is not None)
    assert table.item(trait_row, 3).text() == "Not generated"

    messages: queue.Queue[dict[str, Any]] = queue.Queue()
    window.session.flow_candidate_transcripts[trait_row] = "Candidate described a calm redirect."
    messages.put({"ok": True, "event": "transcripts_updated"})
    fake_timer = SimpleNamespace(stop=lambda: None, deleteLater=lambda: None)

    window._poll_pyside_finalize_worker(messages, fake_timer)
    app.processEvents()

    assert table.item(trait_row, 3).text() == "Candidate described a calm redirect."
    window.window.close()
    app.processEvents()


def _write_two_trait_rubric(tmp_path: Path) -> Path:
    rubric_path = _write_test_rubric(tmp_path)
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["tracks"]["preschool"]["max_weighted_total"] = 10
    rubric["traits"][0]["priority"] = "Standard"
    rubric["traits"].append(
        {
            "id": "trait_2",
            "name": "Reliability",
            "priority": "Standard",
            "weight": 1,
            "applicable_tracks": ["preschool"],
            "primary_question": "Tell me about showing up reliably.",
            "descriptors": {
                "1": "Serious concern",
                "2": "Weak",
                "3": "Mixed / acceptable",
                "4": "Strong",
                "5": "Excellent",
            },
            "sample_answers": {},
        }
    )
    rubric_path.write_text(json.dumps(rubric), encoding="utf-8")
    return rubric_path

def test_pyside_review_apply_scores_updates_history_and_queues_director_referral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    referral_queue_path = tmp_path / "staffing_referrals.pending.jsonl"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_REFERRAL_QUEUE_PATH", referral_queue_path)
    model = build_interview_redesign_model(
        rubric_path=_write_two_trait_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    history = InterviewHistoryStore(history_path)
    history.append(
        {
            "history_id": "hist-review",
            "candidate_name": "Review Candidate",
            "school": "Palmdale",
            "track": "Preschool",
            "interview_date": date.today().isoformat(),
            "outcome": "No Hire",
            "score": "60.0%",
        }
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    window.session.start(candidate_name="Review Candidate", school="Palmdale", track_key="preschool")
    for item in window.session._workflow_items():
        window.session.answers[item.question_id] = {
            "kind": item.kind,
            "title": item.title,
            "prompt": item.prompt,
            "notes": "Saved note",
            "score": "3" if item.kind == "trait" else "",
            "quick_actions": [],
        }
    window._review_history_id = "hist-review"
    window._render_review_page()

    trait_rows = [row for row in range(window.review_question_table.rowCount()) if window.review_question_table.cellWidget(row, 1)]
    first_rating = window.review_question_table.cellWidget(trait_rows[0], 1)
    first_rating.setValue(4)
    app.processEvents()
    window.window.findChild(qt_widgets.QPushButton, "PySideReviewApplyScoresButton").click()
    app.processEvents()

    row = InterviewHistoryStore(history_path).load()[0]
    assert row["outcome"] == "Borderline"
    assert row["review_scores"]["trait_1"] == "4"
    assert window.review_status_label.text().startswith("Scores updated:")
    queued = [json.loads(line) for line in referral_queue_path.read_text(encoding="utf-8").splitlines()]
    assert queued[0]["operation"] == "director_candidate_referral"
    assert queued[0]["payload"]["history_id"] == "hist-review"
    assert queued[0]["payload"]["interviewer_outcome"] == "borderline"

    refreshed_trait_rows = [
        row for row in range(window.review_question_table.rowCount()) if window.review_question_table.cellWidget(row, 1)
    ]
    refreshed_first_rating = window.review_question_table.cellWidget(refreshed_trait_rows[0], 1)
    refreshed_first_rating.setValue(3)
    app.processEvents()
    window.window.findChild(qt_widgets.QPushButton, "PySideReviewApplyScoresButton").click()
    app.processEvents()

    updated_row = InterviewHistoryStore(history_path).load()[0]
    assert updated_row["outcome"] == "No Hire"
    queued = [json.loads(line) for line in referral_queue_path.read_text(encoding="utf-8").splitlines()]
    assert queued[-1]["operation"] == "director_candidate_referral_dismissal"
    assert queued[-1]["payload"]["history_id"] == "hist-review"
    window.window.close()
    app.processEvents()

def test_pyside_finalize_shows_candidate_history_before_report_export_finishes(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Candidates")
    window.session = PySideInterviewSession(model=model, draft_path=tmp_path / "draft.json")
    window.session.start(candidate_name="Mina Patel", school="Palmdale", track_key="preschool")
    monkeypatch.setattr(window, "_stop_pyside_interview_recording", lambda: None)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_HISTORY_PATH", history_path)
    export_started = threading.Event()
    release_export = threading.Event()

    def _blocked_export(_gateway, app_adapter, context):
        export_started.set()
        release_export.wait(timeout=5)
        out_path = tmp_path / "notes.docx"
        app_adapter.state.referral_packet["interview_notes_path"] = str(out_path)
        context.interview_notes_document_path = str(out_path)
        context.payload["interview_notes_document_path"] = str(out_path)
        return str(out_path)

    monkeypatch.setattr(pyside_interview_app.FinalizeGateways, "export_basic_report", _blocked_export)
    monkeypatch.setattr(
        pyside_interview_app.FinalizeGateways,
        "export_integration",
        lambda _gateway, _app_adapter, _context: tmp_path / "integration.json",
    )
    monkeypatch.setattr(
        pyside_interview_app.FinalizeGateways,
        "send_referral",
        lambda _gateway, _app_adapter, _context, _out_path, _integration_path: ({}, None),
    )

    window._generate_interview_notes_from_session()
    for _ in range(100):
        app.processEvents()
        if window.candidate_history_table.rowCount() == 1 and export_started.is_set():
            break

    assert export_started.is_set()
    assert window.history_table.rowCount() == 1
    assert window.history_table.item(0, 1).text() == "Mina Patel"
    assert window.candidate_history_table.rowCount() == 1
    assert window.candidate_history_table.item(0, 1).text() == "Mina Patel"

    release_export.set()
    for _ in range(100):
        app.processEvents()
        if "Interview finalized:" in window.review_status_label.text():
            break
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_history_grid_shows_failed_retry_for_failed_deepseek_row(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx placeholder", encoding="utf-8")
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "interview_notes_path": str(notes_path),
            "deepseek_processing_status": "failed",
            "deepseek_processing_warning": "DeepSeek processing failed.",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    button = window.history_table.cellWidget(0, 6)

    assert button.text() == "Open Notes"
    assert button.isEnabled()
    assert "DeepSeek processing failed." in button.toolTip()
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_failed_retry_button_requeues_deepseek_job(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "failed",
            "deepseek_processing_warning": "DeepSeek processing failed.",
        }
    )
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    job_path.write_text(json.dumps({"history_id": "hist-1"}), encoding="utf-8")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    calls: list[Path] = []

    def _fake_regenerate(job_path: Path, *, mode: str) -> Path:
        calls.append(Path(job_path))
        calls.append(mode)
        InterviewHistoryStore(history_path).update_row(
            "hist-1",
            {"deepseek_processing_status": "processing", "deepseek_processing_warning": ""},
        )
        return Path(job_path).with_suffix(".progress.json")

    monkeypatch.setattr(pyside_interview_app, "regenerate_interview_notes_job", _fake_regenerate)
    window._choose_pyside_notes_regeneration_mode = lambda _row: "full"

    window.history_table.cellWidget(0, 7).click()
    app.processEvents()

    assert calls == [job_path, "full"]
    assert window.history_table.cellWidget(0, 6).text() == "Processing"
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_retry_uses_persisted_deepseek_job_path(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    stored_job_path = tmp_path / "archived_jobs" / "deepseek-finalize-hist-1.json"
    stored_job_path.parent.mkdir()
    stored_job_path.write_text(json.dumps({"history_id": "hist-1"}), encoding="utf-8")
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "failed",
            "deepseek_job_path": str(stored_job_path),
            "deepseek_progress_path": str(stored_job_path.with_suffix(".progress.json")),
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    calls: list[object] = []

    monkeypatch.setattr(
        pyside_interview_app,
        "regenerate_interview_notes_job",
        lambda path, *, mode: calls.extend([Path(path), mode]) or Path(path).with_suffix(".progress.json"),
    )
    window._choose_pyside_notes_regeneration_mode = lambda _row: "full"

    window.history_table.cellWidget(0, 7).click()
    app.processEvents()

    assert calls == [stored_job_path, "full"]
    window.window.close()
    app.processEvents()

def test_pyside_existing_notes_can_be_regenerated(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx", encoding="utf-8")
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "interview_notes_path": str(notes_path),
            "deepseek_processing_status": "complete",
        }
    )
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    job_path.write_text(json.dumps({"history_id": "hist-1"}), encoding="utf-8")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    calls: list[object] = []

    monkeypatch.setattr(
        pyside_interview_app,
        "regenerate_interview_notes_job",
        lambda path, *, mode: calls.extend([Path(path), mode]) or Path(path).with_suffix(".progress.json"),
    )
    window._choose_pyside_notes_regeneration_mode = lambda _row: "document_only"

    window.history_table.cellWidget(0, 7).click()
    app.processEvents()

    assert calls == [job_path, "document_only"]
    window.window.close()
    app.processEvents()

def test_pyside_regenerate_prompt_uses_history_candidate_name(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "complete",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    calls: list[str] = []

    class _FakeMessageBox:
        AcceptRole = object()
        ActionRole = object()
        Cancel = object()

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, title: str) -> None:
            calls.append(f"title:{title}")

        def setText(self, text: str) -> None:
            calls.append(f"text:{text}")

        def setInformativeText(self, text: str) -> None:
            calls.append(f"info:{text}")

        def addButton(self, *args):
            button = object()
            if args and args[0] == "Document Only":
                self._clicked = button
            return button

        def setDefaultButton(self, _button) -> None:
            calls.append("default")

        def exec(self) -> None:
            calls.append("exec")

        def clickedButton(self):
            return self._clicked

    monkeypatch.setattr(window.QtWidgets, "QMessageBox", _FakeMessageBox)

    mode = window._choose_pyside_notes_regeneration_mode(window.model.home.history_rows[0])

    assert mode == "document_only"
    assert "text:Regenerate interview notes for Latoya Nugent?" in calls
    assert "exec" in calls
    window.window.close()
    app.processEvents()

def test_pyside_open_notes_opens_existing_document_without_regenerate_prompt(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx", encoding="utf-8")
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "interview_notes_path": str(notes_path),
            "deepseek_processing_status": "complete",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    calls: list[str] = []
    monkeypatch.setattr(window, "_choose_pyside_notes_regeneration_mode", lambda _row: calls.append("prompt") or "full")
    if sys.platform.startswith("win"):
        monkeypatch.setattr(pyside_interview_app.os, "startfile", lambda path: calls.append(f"open:{path}"))
    else:
        monkeypatch.setattr(pyside_interview_app.subprocess, "run", lambda args, check: calls.append(f"open:{args[-1]}"))

    window.history_table.cellWidget(0, 6).click()
    app.processEvents()

    assert calls == [f"open:{notes_path}"]
    window.window.close()
    app.processEvents()

def test_pyside_regenerate_prompts_before_missing_job_warning(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "deepseek_processing_status": "complete",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    calls: list[str] = []
    window._choose_pyside_notes_regeneration_mode = lambda _row: calls.append("mode") or "document_only"
    monkeypatch.setattr(
        window.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: calls.append(f"warning:{message}"),
    )

    window.history_table.cellWidget(0, 7).click()
    app.processEvents()

    assert calls == ["mode", "warning:DeepSeek job file was not found."]
    window.window.close()
    app.processEvents()

def test_pyside_review_source_exposes_finalize_button_not_placeholder_notes() -> None:
    source = Path("src/pyside_interview_app.py").read_text(encoding="utf-8")

    assert 'self._primary_button("Finalize Interview")' not in source
    assert 'output_dir=DEFAULT_BASE_DIR / "pyside_notes"' not in source

def test_pyside_onboarding_board_surfaces_next_required_task() -> None:
    employee = Employee(
        id="emp-1",
        name="Taylor Green",
        acceptance_date="2026-06-01",
        start_date="2026-06-10",
        school="Palmdale",
        tasks=[
            EmployeeTask(id="past", template_id="", title="Past task", due_date="2026-06-09"),
            EmployeeTask(id="today", template_id="", title="Today task", due_date="2026-06-10"),
            EmployeeTask(id="done", template_id="", title="Done task", due_date="2026-06-08", completed=True),
        ],
    )

    board = build_pyside_onboarding_board(
        employees=[employee],
        scheduler_settings={"critical_window_days": 2},
        today=date(2026, 6, 10),
    )

    assert board.overdue == 1
    assert board.due_today == 1
    assert board.next_task == "Taylor Green: Past task"
    assert board.rows[0]["employee"] == "Taylor Green"
    assert board.rows[0]["next_task"] == "Past task"

def test_pyside_candidate_board_groups_history_by_candidate() -> None:
    rows = [
        {
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "track": "Preschool",
            "outcome": "Hire",
            "percent_of_max": 85.0,
            "next_action": "Generate Offer",
        },
        {
            "candidate_name": "Dalia Gaspar",
            "school": "Hawthorne",
            "track": "Preschool",
            "outcome": "No Hire",
            "percent_of_max": 60.0,
            "next_action": "Return Home",
        },
    ]

    board = build_pyside_candidate_board(rows)

    assert board.total_candidates == 2
    assert board.rows[0]["candidate"] == "Latoya Nugent"
    assert board.rows[0]["status"] == "Hire"
    assert board.rows[0]["next_action"] == "Generate Offer"

def test_pyside_candidate_board_recomputes_stale_history_status_from_score() -> None:
    rows = [
        {
            "candidate_name": "Tatiana",
            "school": "Palmdale",
            "track": "Preschool",
            "determination": "No Hire",
            "interview_score": 70.0,
            "offer_status": "not_generated",
        },
    ]

    board = build_pyside_candidate_board(rows)

    assert board.rows[0]["score"] == "70.0"
    assert board.rows[0]["status"] == "Borderline"
    assert board.history_rows[0].status == "Borderline"

def test_pyside_candidate_board_treats_ai_auto_no_hire_as_advisory_for_status() -> None:
    rows = [
        {
            "candidate_name": "Tatiana",
            "school": "Palmdale",
            "track": "Preschool",
            "determination": "No Hire",
            "interview_score": 70.0,
            "auto_no_hire_present": True,
            "locked_rule": "DeepSeek automatic no-hire signal observed => Immediate NO HIRE",
            "offer_status": "not_generated",
        },
    ]

    board = build_pyside_candidate_board(rows)

    assert board.rows[0]["score"] == "70.0"
    assert board.rows[0]["status"] == "Borderline"
    assert board.history_rows[0].status == "Borderline"

def test_pyside_candidates_page_uses_history_table_layout_and_actions(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx placeholder", encoding="utf-8")
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "interview_date": "2026-06-23",
            "outcome": "Hire",
            "offer_status": "not_generated",
            "interview_notes_path": str(notes_path),
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Candidates")

    assert window.candidate_history_table.columnCount() == window.history_table.columnCount() == 11
    assert [
        window.candidate_history_table.horizontalHeaderItem(column).text()
        for column in range(window.candidate_history_table.columnCount())
    ] == [
        "Date",
        "Candidate",
        "School",
        "Position",
        "Score",
        "Status",
        "Notes",
        "Regenerate",
        "Transcript",
        "Offer",
        "Delete",
    ]
    assert window.candidate_history_table.item(0, 1).text() == "Latoya Nugent"
    assert window.candidate_history_table.cellWidget(0, 6).text() == "Open Notes"
    assert window.candidate_history_table.cellWidget(0, 7).text() == "Regenerate"
    assert window.candidate_history_table.cellWidget(0, 8).text() == "Import"
    assert window.candidate_history_table.cellWidget(0, 9).text() == "Generate Offer"
    assert window.candidate_history_table.cellWidget(0, 10).text() == "Delete"
    window.window.close()
    app.processEvents()

def test_pyside_history_delete_requires_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    store = InterviewHistoryStore(history_path)
    store.append({"history_id": "hist-1", "candidate_name": "Latoya Nugent"})
    store.append({"history_id": "hist-2", "candidate_name": "Dana Teacher"})
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    no = window.QtWidgets.QMessageBox.StandardButton.No
    yes = window.QtWidgets.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: no)
    window._delete_history_row(model.home.history_rows[0])
    assert [row["history_id"] for row in InterviewHistoryStore(history_path).load()] == ["hist-1", "hist-2"]

    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: yes)
    hist_1_row = next(row for row in window.model.home.history_rows if row.row_key == "hist-1")
    window._delete_history_row(hist_1_row)
    assert [row["history_id"] for row in InterviewHistoryStore(history_path).load()] == ["hist-2"]
    window.window.close()
    app.processEvents()

def test_pyside_admin_studio_uses_guided_readonly_sections_until_edit(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["tracks"]["infant_toddler"] = {"label": "Infant/Toddler", "max_weighted_total": 5}
    rubric_payload["traits"][0]["sample_answers"] = {
        "1": "Concern sample",
        "2": "Weak sample",
        "3": "Mixed sample",
        "4": "Strong sample",
        "5": "Excellent sample",
    }
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:14b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    questions_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioQuestionsTable")
    rubrics_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioRubricsTable")
    model_selector = window.window.findChild(qt_widgets.QComboBox, "AdminStudioDeepseekModelSelector")
    notification_template_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationTemplateButton")

    assert section_list is not None
    assert [section_list.item(index).text() for index in range(section_list.count())] == [
        "Configuration",
        "Admin Dashboard",
        "Questions & Flow",
        "Rubrics",
        "Signal Hints",
        "Templates & Folders",
        "Notifications",
        "AI Settings",
        "DeepSeek Model",
        "DeepSeek Prompts",
        "System",
        "Advanced JSON",
        "Validation",
        "Email Settings",
    ]
    assert not (section_list.item(0).flags() & qt_core.Qt.ItemFlag.ItemIsEnabled)
    assert section_list.currentItem().text() == "Admin Dashboard"
    assert window.window.findChildren(qt_widgets.QFrame, "AdminStudioConceptPanel") == []
    assert "Unsaved changes: 0" in window.admin_status_label.text()
    assert questions_table.wordWrap() is True
    assert questions_table.textElideMode() == qt_core.Qt.TextElideMode.ElideNone
    assert "alternate-background-color: #f8fafc" in questions_table.styleSheet()
    assert "selection-color: #ffffff" in questions_table.styleSheet()
    assert questions_table.item(0, 4).text() == "Why Launch Pad Learning?"
    assert not (questions_table.item(0, 4).flags() & qt_core.Qt.ItemFlag.ItemIsEditable)
    assert rubrics_table.rowCount() == 1
    assert rubrics_table.item(0, 0).text() == "trait_1"
    assert rubrics_table.item(0, 1).text() == "Empathy"
    assert not (rubrics_table.item(0, 0).flags() & qt_core.Qt.ItemFlag.ItemIsEditable)
    assert model_selector is not None
    assert [model_selector.itemData(index) for index in range(model_selector.count())] == [
        "deepseek-r1:1.5b",
        "deepseek-r1:8b",
        "deepseek-r1:14b",
    ]
    assert model_selector.currentData() == "deepseek-r1:14b"
    assert model_selector.isEnabled() is False
    assert notification_template_button is not None
    assert notification_template_button.isEnabled() is False
    window.admin_edit_button.click()
    assert window.admin_edit_button.text() == "Editing active"
    assert "Edit mode" in window.admin_status_label.text()
    assert questions_table.item(0, 4).flags() & qt_core.Qt.ItemFlag.ItemIsEditable
    assert "Editable" in questions_table.item(0, 4).toolTip()
    assert rubrics_table.item(0, 1).flags() & qt_core.Qt.ItemFlag.ItemIsEditable
    assert model_selector.isEnabled() is True
    assert notification_template_button.isEnabled() is True
    window.window.close()
    app.processEvents()

def test_pyside_pages_do_not_force_content_wider_than_viewport(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.window.show()
    window.window.resize(640, 480)
    app.processEvents()

    page_scrolls = window.window.findChildren(qt_widgets.QScrollArea, "PySidePageScrollArea")

    assert page_scrolls
    for scroll in page_scrolls:
        assert scroll.horizontalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert scroll.widgetResizable() is True
        assert scroll.minimumWidth() == 0
        assert scroll.widget().minimumWidth() <= scroll.viewport().width()
        assert scroll.widget().width() <= scroll.viewport().width() + 2
        assert scroll.widget().sizePolicy().horizontalPolicy() == qt_widgets.QSizePolicy.Policy.Expanding
    window.window.close()
    app.processEvents()

def test_pyside_admin_studio_modern_dashboard_and_section_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["tracks"]["infant_toddler"] = {"label": "Infant/Toddler", "max_weighted_total": 5}
    rubric_payload["traits"][0]["sample_answers"] = {
        "1": "Concern sample",
        "2": "Weak sample",
        "3": "Mixed sample",
        "4": "Strong sample",
        "5": "Excellent sample",
    }
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Hawthorne": {"interview_notes_dir": "C:/safe/hawthorne"},
                "Long Beach": {"interview_notes_dir": "C:/safe/long-beach"},
                "North Long Beach": {"interview_notes_dir": "C:/safe/nlb"},
                "Palmdale": {"interview_notes_dir": ""},
            }
        ),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Approved: {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")

    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioWorkspaceActionsLabel").text() == "Workspace actions"
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSidebarBrandCard") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioMetricStrip") is not None
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioTracksPill").text().startswith("Tracks:")
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionsPill").text().startswith("Questions:")
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioUnsavedPill").text() == "Unsaved changes: 0"
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioValidationPill").text().startswith("Validation")
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioSaveDraftButton").text() == "Save Draft"
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioPublishButton").text() == "Publish Changes"
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioPublishButton").property("adminButtonRole") == "primary"
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioDiscardButton").property("adminButtonRole") == "danger"
    assert window.window.findChild(qt_widgets.QWidget, "AdminStudioQuestionsTableContainer").property("adminBackingField") is True
    assert window.window.findChild(qt_widgets.QWidget, "AdminStudioRubricsTableContainer").property("adminBackingField") is True
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    assert section_list.verticalScrollMode() == qt_widgets.QAbstractItemView.ScrollMode.ScrollPerPixel
    assert section_list.verticalScrollBar().singleStep() == 24
    questions_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioQuestionsTable")
    assert questions_table.verticalScrollMode() == qt_widgets.QAbstractItemView.ScrollMode.ScrollPerPixel
    assert questions_table.verticalScrollBar().singleStep() == 24
    environment_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioEnvironmentCard")
    user_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioUserCard")
    assert "Production" in _widget_text(environment_card)
    assert "v" in _widget_text(environment_card)
    assert "Super Admin" in _widget_text(user_card)
    dashboard_cards = window.window.findChildren(qt_widgets.QFrame, "AdminStudioDashboardCard")
    assert len(dashboard_cards) >= 9
    dashboard_text = " ".join(_widget_text(card) for card in dashboard_cards)
    assert "Questions & Flow" in dashboard_text
    assert "Validation" in dashboard_text
    for card in dashboard_cards:
        icon_label = card.findChild(qt_widgets.QLabel, "AdminStudioDashboardCardIcon")
        assert icon_label is not None
        assert icon_label.pixmap() is not None
        assert not icon_label.pixmap().isNull()
    validation_review = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationReviewPanel")
    assert validation_review is not None
    assert "Active notification rule 'offer.approved' requires a subject template." in _widget_text(validation_review)
    issue_button = validation_review.findChild(qt_widgets.QPushButton, "AdminStudioValidationReviewIssue")
    assert issue_button is not None
    assert "offer.approved" in issue_button.text()
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioDraftChangesPanel") is not None
    publishing_readiness = window.window.findChild(qt_widgets.QFrame, "AdminStudioPublishingReadinessPanel")
    assert publishing_readiness is not None
    readiness_text = _widget_text(publishing_readiness)
    assert "Folder health:" in readiness_text
    assert "Prompt validation:" in readiness_text
    assert "Notification completeness:" in readiness_text
    assert "JSON file health:" in readiness_text
    assert "Needs attention" in readiness_text
    assert len(publishing_readiness.findChildren(qt_widgets.QFrame, "AdminStudioPublishingReadinessRow")) == 4
    quick_links = window.window.findChild(qt_widgets.QFrame, "AdminStudioQuickLinksPanel")
    assert quick_links is not None
    quick_link_buttons = quick_links.findChildren(qt_widgets.QPushButton)
    assert [button.text() for button in quick_link_buttons] == [
        "Create / Modify Notification Template",
        "Browse School Folders",
        "Open Prompt Template Editor",
        "View Validation Rules",
        "View All Settings",
    ]

    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    window.admin_edit_button.click()
    quick_link_buttons[0].click()
    assert section_list.currentItem().text() == "Notifications"
    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent").text() == ""
    assert "new notification rule" in window.window.findChild(qt_widgets.QLabel, "AdminStudioNotificationEditorMeta").text()
    window._set_admin_editing_enabled(False)
    section_list.setCurrentRow(1)
    for row in range(section_list.count()):
        item = section_list.item(row)
        if item.data(window.QtCore.Qt.ItemDataRole.UserRole) != "group":
            assert not item.icon().isNull(), item.text()
    issue_button.click()
    assert section_list.currentItem().text() == "Notifications"
    section_list.setCurrentRow(1)
    quick_link_buttons[1].click()
    assert section_list.currentItem().text() == "Templates & Folders"
    section_list.setCurrentRow(1)
    quick_link_buttons[2].click()
    assert section_list.currentItem().text() == "DeepSeek Prompts"
    section_list.setCurrentRow(1)
    quick_link_buttons[3].click()
    assert section_list.currentItem().text() == "Validation"
    section_list.setCurrentRow(1)
    quick_link_buttons[4].click()
    assert section_list.currentItem().text() == "Advanced JSON"
    section_list.setCurrentRow(1)
    publishing_readiness.findChild(qt_widgets.QPushButton, "AdminStudioPublishingReadinessPanel_View_System_Health").click()
    assert section_list.currentItem().text() == "Advanced JSON"
    section_list.setCurrentRow(2)
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioQuestionCard")) >= 1
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioQuestionEditDrawer") is not None
    section_list.setCurrentRow(3)
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioTraitCard_trait_1") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioTraitDetailPanel") is not None
    section_list.setCurrentRow(4)
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioSignalHintGroup")) >= 1
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalDetailPanel") is not None
    section_list.setCurrentRow(8)
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioModelOptionCard")) == 3
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSelectedModelPanel") is not None
    fast_model_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSelectModel_deepseek-r1_1_5b")
    assert fast_model_button is not None
    assert fast_model_button.isEnabled() is False
    window.admin_edit_button.click()
    assert fast_model_button.isEnabled() is True
    fast_model_button.click()
    model_selector = window.window.findChild(qt_widgets.QComboBox, "AdminStudioDeepseekModelSelector")
    assert model_selector.currentData() == "deepseek-r1:1.5b"
    window.admin_save_draft_button.click()
    assert "Unsaved changes: 0" not in window.admin_status_label.text()
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioUnsavedPill").text() != "Unsaved changes: 0"
    section_list.setCurrentRow(5)
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioSchoolFolderCard")) >= 4
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSchoolDetailDrawer") is not None
    section_list.setCurrentRow(6)
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")) >= 1
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationEditPanel") is not None
    section_list.setCurrentRow(9)
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioPromptTemplateCard")) >= 1
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioPromptInspectorPanel") is not None
    section_list.setCurrentRow(11)
    json_cards = [
        frame
        for frame in window.window.findChildren(qt_widgets.QFrame)
        if frame.objectName().startswith("AdminStudioJsonFileCard_")
    ]
    assert len(json_cards) >= 5
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonFileDetailPanel") is not None
    section_list.setCurrentRow(12)
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioPublishAvailabilityPanel") is not None

    window.window.close()
    app.processEvents()

def test_pyside_admin_deepseek_model_screen_shows_hardware_and_ollama_guidance(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(8)

    hardware = window.window.findChild(qt_widgets.QFrame, "AdminStudioDeepseekHardwareNotesPanel")
    ollama = window.window.findChild(qt_widgets.QFrame, "AdminStudioDeepseekOllamaCompatibilityPanel")
    performance = window.window.findChild(qt_widgets.QFrame, "AdminStudioDeepseekPerformancePanel")

    assert hardware is not None
    assert "More RAM/VRAM improves context length" in _widget_text(hardware)
    assert "View hardware recommendations" in _widget_text(hardware)
    assert ollama is not None
    assert "All listed models are allowlisted and compatible with Ollama" in _widget_text(ollama)
    assert "View allowlisted DeepSeek models" in _widget_text(ollama)
    assert performance is not None
    assert "Response time" in _widget_text(performance)
    assert "Context window" in _widget_text(performance)
    window.window.close()
    app.processEvents()

def test_pyside_admin_deepseek_model_uses_cards_not_visible_dropdown(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(8)

    model_selector = window.window.findChild(qt_widgets.QComboBox, "AdminStudioDeepseekModelSelector")
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioModelOptionCard")) == 3
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSelectedModelPanel") is not None
    assert model_selector is not None
    assert model_selector.property("adminBackingField") is True
    assert model_selector.isVisibleTo(window.window) is False
    assert model_selector.maximumHeight() == 0
    window.window.close()
    app.processEvents()

def test_pyside_admin_modern_sections_hide_legacy_backing_tables(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({"Palmdale": {"interview_notes_dir": "C:/safe/palmdale"}}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")

    backing_tables = {
        2: "AdminStudioQuestionsTable",
        3: "AdminStudioRubricsTable",
        6: "AdminStudioNotificationsTable",
        9: "AdminStudioPromptsTable",
    }
    for row, object_name in backing_tables.items():
        section_list.setCurrentRow(row)
        table = window.window.findChild(qt_widgets.QTableWidget, object_name)
        assert table is not None
        assert table.property("adminBackingField") is True
        assert table.isVisibleTo(window.window) is False
        assert table.maximumHeight() == 0

    window.window.close()
    app.processEvents()

def test_pyside_admin_deepseek_selected_model_panel_shows_decision_details(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(8)

    selected_panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioSelectedModelPanel")
    text = _widget_text(selected_panel)

    assert "Balanced - DeepSeek R1 8B" in text
    assert "Speed: Fast" in text
    assert "Quality: Very Good" in text
    assert "Why this model?" in text
    assert "Publish restrictions" in text
    assert "Only allowlisted local Ollama DeepSeek models can be published" in text
    assert selected_panel.findChild(qt_widgets.QPushButton, "AdminStudioUseSelectedModelButton") is not None
    assert selected_panel.findChild(qt_widgets.QPushButton, "AdminStudioViewModelDetailsButton") is not None
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_issue_action_opens_affected_section(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="alpha.ready",
            label="Other rule",
            active=False,
            subject_template="Other",
            body_template="Other body",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")

    section_list.setCurrentRow(12)
    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")
    assert issue_card is not None
    action = issue_card.findChild(qt_widgets.QPushButton, "AdminStudioValidationIssueAction")
    assert action is not None
    assert action.text() == "Open Notifications"

    action.click()

    assert section_list.currentItem().text() == "Notifications"
    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent").text() == "offer.approved"
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_issue_action_selects_affected_prompt(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(
        json.dumps(
                        {
                            "answer_summary_user": "Summarize {payload_json}.",
                            "executive_summary_user": "Original executive summary from {transcript}.",
                        }
        ),
        encoding="utf-8",
    )
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    window.admin_draft.update_prompt("executive_summary_user", "Changed executive summary from {transcript}.")

    section_list.setCurrentRow(12)
    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")
    action = issue_card.findChild(qt_widgets.QPushButton, "AdminStudioValidationIssueAction")

    assert action.text() == "Open DeepSeek Prompts"

    action.click()

    assert section_list.currentItem().text() == "DeepSeek Prompts"
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptEditorTitle").text() == "executive_summary_user"
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_issue_action_selects_affected_json_file_and_line(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = tmp_path / "question_overrides.json"
    overrides_path.write_text('{\n  "tracks": [\n', encoding="utf-8")
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    overrides_path.write_text('{\n  "tracks": [\n', encoding="utf-8")
    window = _pyside_window_on_page(model, "Admin")
    window.admin_draft.validate = lambda: ["Question override file has invalid JSON on line 3."]
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")

    section_list.setCurrentRow(12)
    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")
    action = issue_card.findChild(qt_widgets.QPushButton, "AdminStudioValidationIssueAction")

    assert action.text() == "Open Advanced JSON"

    action.click()

    assert section_list.currentItem().text() == "Advanced JSON"
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonSelectedFile").text() == "question_overrides.json"
    assert "Line 3" in window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonViewerFooter").text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_details_dialog_shows_technical_payload(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(12)

    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")
    details = issue_card.findChild(qt_widgets.QPushButton, "AdminStudioValidationDetailsButton")
    assert details is not None
    details.click()
    dialog = window.admin_validation_details_dialog

    assert dialog.objectName() == "AdminStudioValidationDetailsDialog"
    assert "Validation Details" in dialog.windowTitle()
    text = _widget_text(dialog)
    assert "Technical details" in text
    assert "Raw validation output" in text
    assert "offer.approved" in text
    raw = dialog.findChild(qt_widgets.QPlainTextEdit, "AdminStudioValidationRawOutput")
    assert raw is not None
    assert raw.isReadOnly() is True
    assert "Active notification rule" in raw.toPlainText()
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_review_shows_blocked_banner_summary_and_guidance(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": ""}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "not-allowlisted"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(12)

    banner = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationBlockedBanner")
    summary_cards = window.window.findChildren(qt_widgets.QFrame, "AdminStudioValidationSummaryCard")
    guidance = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationGuidancePanel")
    last_run = window.window.findChild(qt_widgets.QFrame, "AdminStudioLastValidationRunPanel")
    publish = window.window.findChild(qt_widgets.QFrame, "AdminStudioPublishAvailabilityPanel")

    assert banner is not None
    assert "Publishing blocked" in _widget_text(banner)
    assert "issues" in _widget_text(banner)
    assert len(summary_cards) == 4
    summary_text = " ".join(_widget_text(card) for card in summary_cards)
    assert "Blocking" in summary_text
    assert "Warnings" in summary_text
    assert "Passed checks" in summary_text
    assert guidance is not None
    assert "Review each blocking issue" in _widget_text(guidance)
    assert last_run is not None
    assert "Environment: Production" in _widget_text(last_run)
    assert "Publish blocked" in _widget_text(publish)
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_review_issue_cards_show_filter_why_and_what(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(12)

    filter_control = window.window.findChild(qt_widgets.QComboBox, "AdminStudioValidationIssueFilter")
    filter_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioValidationFilterButton")
    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")

    assert filter_control is not None
    assert filter_control.currentText() == "Blocking only"
    assert filter_button is not None
    assert "Filter" in filter_button.text()
    card_text = _widget_text(issue_card)
    assert "Why this matters" in card_text
    assert "blank subject" in card_text
    assert "What to do" in card_text
    assert "Add a subject template" in card_text
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_issue_card_has_collapsed_raw_details(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(12)

    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")
    details = issue_card.findChild(qt_widgets.QGroupBox, "AdminStudioValidationInlineDetails")
    raw = issue_card.findChild(qt_widgets.QPlainTextEdit, "AdminStudioValidationInlineRawOutput")

    assert details is not None
    assert details.isCheckable() is True
    assert details.isChecked() is False
    assert "Technical details" in details.title()
    assert raw is not None
    assert raw.isHidden() is True
    details.setChecked(True)
    app.processEvents()
    assert raw.isHidden() is False
    assert '"severity": "blocking"' in raw.toPlainText()
    assert "offer.approved" in raw.toPlainText()
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_issue_filter_hides_nonmatching_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(12)

    filter_control = window.window.findChild(qt_widgets.QComboBox, "AdminStudioValidationIssueFilter")
    filter_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioValidationFilterButton")
    issue_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationIssueCard")

    assert issue_card.property("adminValidationFilterMatch") is True
    filter_control.setCurrentText("Warnings only")
    filter_button.click()

    assert issue_card.property("adminValidationFilterMatch") is False
    assert issue_card.isHidden() is True

    filter_control.setCurrentText("Blocking only")
    filter_button.click()

    assert issue_card.property("adminValidationFilterMatch") is True
    assert issue_card.isHidden() is False
    window.window.close()
    app.processEvents()

def test_pyside_admin_validation_publish_availability_uses_disabled_button_when_blocked(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Body {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(12)

    panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioPublishAvailabilityPanel")
    button = panel.findChild(qt_widgets.QPushButton, "AdminStudioValidationPublishAvailabilityButton")

    assert button is not None
    assert button.text() == "Publish blocked"
    assert button.isEnabled() is False
    assert "You can publish once all blocking issues are resolved." in _widget_text(panel)
    window.window.close()
    app.processEvents()

def test_pyside_admin_publish_button_tracks_validation_blockers_after_fix(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Approved: {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.admin_edit_button.click()

    assert window.admin_publish_button.isEnabled() is False
    assert "Validation blocked" in window.admin_validation_pill.text()

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_offer_approved").click()
    subject = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleSubject")
    subject.setText("Offer approved for {candidate_name}")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave").click()

    assert window.admin_validation_pill.text() == "Validation: ready"
    assert window.admin_publish_button.isEnabled() is True
    window.window.close()
    app.processEvents()

def test_pyside_admin_review_changes_button_opens_grouped_diff_dialog(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.admin_edit_button.click()
    prompt_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user")
    prompt_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    note = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioPromptVersionNote")
    editor.setPlainText("Updated review prompt.")
    note.setText("Explain review prompt change.")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave").click()

    window.admin_review_button.click()
    app.processEvents()

    dialog = next(
        widget
        for widget in app.topLevelWidgets()
        if widget.objectName() == "AdminStudioReviewChangesDialog" and widget.isVisible()
    )
    assert "Review Changes" in dialog.windowTitle()
    section_card = dialog.findChild(qt_widgets.QFrame, "AdminStudioReviewChangedSectionCard")
    assert section_card is not None
    assert "DeepSeek Prompts" in _widget_text(section_card)
    assert dialog.findChild(qt_widgets.QFrame, "AdminStudioReviewChangedFileCard") is not None
    assert "deepseek_prompts.json" in _widget_text(dialog)
    assert "Updated review prompt." in _widget_text(dialog)
    final_confirmation = dialog.findChild(qt_widgets.QCheckBox, "AdminStudioReviewFinalConfirmation")
    publish = dialog.findChild(qt_widgets.QPushButton, "AdminStudioReviewPublishButton")
    assert final_confirmation is not None
    assert publish is not None
    assert publish.isEnabled() is False
    final_confirmation.setChecked(True)
    assert publish.isEnabled() is True
    dialog.close()
    window.window.close()
    app.processEvents()

def test_pyside_admin_dashboard_draft_changes_lists_all_dirty_files(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.admin_edit_button.click()
    prompt_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user")
    prompt_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    note = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioPromptVersionNote")
    editor.setPlainText("Dashboard-visible prompt change.")
    note.setText("Explain dashboard prompt change.")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave").click()
    window.admin_draft.rubric["traits"][0]["name"] = "Dashboard Empathy"
    window.admin_draft.add_custom_question("preschool", "dashboard-question", "Dashboard question", "What should dashboard show?", section="Qualification", position=1)
    window.admin_draft.update_school_settings("Palmdale", {"offer_output_dir": str(tmp_path / "offers")})
    window.admin_draft.update_deepseek_model("deepseek-r1:14b")
    window.admin_draft.update_notification_rule(
        "custom.dashboard",
        {
            "label": "Dashboard rule",
            "active": "true",
            "subject_template": "Dashboard subject",
            "body_template": "Dashboard body",
            "recipients": "director@example.org",
        },
    )

    section_list.setCurrentRow(0)
    app.processEvents()

    panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioDraftChangesPanel")
    assert panel is not None
    panel_text = _widget_text(panel)
    assert "6 Unsaved" in panel_text
    for filename in (
        "rubric.json",
        "question_overrides.json",
        "school_offer_settings.json",
        "deepseek_prompts.json",
        "interview_app_settings.json",
        "notification_rules.sqlite3",
    ):
        assert filename in panel_text
    assert "Dashboard-visible prompt change." in panel_text
    assert len(panel.findChildren(qt_widgets.QFrame, "AdminStudioDraftChangeRow")) == 6
    window.window.close()
    app.processEvents()

def test_pyside_admin_dashboard_validation_issue_opens_affected_notification_rule(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="aaa.first",
            label="First valid rule",
            active=True,
            subject_template="Valid",
            body_template="Body",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="",
            body_template="Approved: {candidate_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    validation_review = window.window.findChild(qt_widgets.QFrame, "AdminStudioValidationReviewPanel")
    issue_button = next(
        button
        for button in validation_review.findChildren(qt_widgets.QPushButton, "AdminStudioValidationReviewIssue")
        if "offer.approved" in button.text()
    )

    issue_button.click()
    app.processEvents()

    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    assert section_list.currentItem().text() == "Notifications"
    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent").text() == "offer.approved"
    window.window.close()
    app.processEvents()

def test_pyside_admin_publish_confirmation_summarizes_changes_and_validation(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.admin_edit_button.click()
    prompt_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user")
    prompt_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    note = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioPromptVersionNote")
    editor.setPlainText("Ready to publish prompt.")
    note.setText("Explain publish prompt change.")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave").click()

    summary = window.admin_draft.change_summary()
    dialog = window._build_admin_publish_confirmation_dialog(summary)

    assert dialog.objectName() == "AdminStudioPublishConfirmationDialog"
    assert "Publish Confirmation" in dialog.windowTitle()
    assert "Validation: ready" in _widget_text(dialog)
    section_card = dialog.findChild(qt_widgets.QFrame, "AdminStudioPublishSectionSummaryCard")
    assert section_card is not None
    assert "DeepSeek Prompts" in _widget_text(section_card)
    assert "deepseek_prompts.json" in _widget_text(dialog)
    assert "Ready to publish prompt." in _widget_text(dialog)
    final_confirmation = dialog.findChild(qt_widgets.QCheckBox, "AdminStudioPublishFinalConfirmation")
    confirm = dialog.findChild(qt_widgets.QPushButton, "AdminStudioConfirmPublishButton")
    assert final_confirmation is not None
    assert confirm is not None
    assert confirm.isEnabled() is False
    assert json.loads(prompts_path.read_text(encoding="utf-8"))["answer_summary_user"] == "Summarize."
    final_confirmation.setChecked(True)
    assert confirm.isEnabled() is True
    confirm.click()

    assert json.loads(prompts_path.read_text(encoding="utf-8"))["answer_summary_user"] == "Ready to publish prompt."
    window.window.close()
    app.processEvents()

def test_pyside_admin_question_card_edits_through_drawer_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(2)

    question_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionCardButton_Why_LPL")
    drawer_id = window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerId")
    drawer_text = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioQuestionDrawerText")
    drawer_text_count = window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerTextCounter")
    drawer_notes_count = window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerNotesCounter")
    drawer_type = window.window.findChild(qt_widgets.QComboBox, "AdminStudioQuestionDrawerType")
    drawer_linked_trait = window.window.findChild(qt_widgets.QComboBox, "AdminStudioQuestionDrawerLinkedTrait")
    drawer_required = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioQuestionDrawerRequired")
    drawer_scoring = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioQuestionDrawerScore")
    drawer_scoring_weight = window.window.findChild(qt_widgets.QComboBox, "AdminStudioQuestionDrawerScoringWeight")
    drawer_flag_weak = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioQuestionDrawerFlagWeak")
    drawer_save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionDrawerSave")
    add_question = window.window.findChild(qt_widgets.QPushButton, "AdminStudioAddQuestionDropZone")

    assert question_button is not None
    assert add_question is not None
    assert add_question.text() == "+ Add Question"
    assert drawer_id.text() == "Why-LPL"
    assert drawer_text.toPlainText() == "Why Launch Pad Learning?"
    assert drawer_text_count.text() == "24 / 1000"
    assert drawer_notes_count.text().endswith(" / 500")
    assert drawer_text.isEnabled() is False
    assert drawer_type is not None
    assert drawer_type.currentText() == "custom"
    assert drawer_type.isEnabled() is False
    assert drawer_linked_trait is not None
    assert drawer_linked_trait.itemText(0) == "Select a trait (optional)"
    assert "Empathy" in [drawer_linked_trait.itemText(index) for index in range(drawer_linked_trait.count())]
    assert drawer_linked_trait.isEnabled() is False
    assert drawer_required is not None
    assert drawer_required.isChecked() is True
    assert drawer_required.isEnabled() is False
    assert drawer_scoring is not None
    assert drawer_scoring.isChecked() is True
    assert drawer_scoring.isEnabled() is False
    assert drawer_scoring_weight is not None
    assert drawer_scoring_weight.currentText() == "Standard (1x)"
    assert [drawer_scoring_weight.itemText(index) for index in range(drawer_scoring_weight.count())] == [
        "Standard (1x)",
        "Higher",
        "Lower",
        "Custom",
    ]
    assert drawer_scoring_weight.isEnabled() is False
    assert drawer_flag_weak is not None
    assert drawer_flag_weak.text() == "Flag for review"
    assert drawer_flag_weak.isChecked() is False
    assert drawer_flag_weak.isEnabled() is False
    assert drawer_save.isEnabled() is False

    window.admin_edit_button.click()
    question_button.click()
    assert drawer_text.isEnabled() is True
    assert drawer_type.isEnabled() is True
    assert drawer_linked_trait.isEnabled() is True
    assert drawer_required.isEnabled() is True
    assert drawer_scoring.isEnabled() is True
    assert drawer_scoring_weight.isEnabled() is True
    assert drawer_flag_weak.isEnabled() is True
    drawer_text.setPlainText("Why are you applying to Launch Pad Learning now?")
    assert drawer_text_count.text() == "48 / 1000"
    drawer_save.click()
    window.admin_save_draft_button.click()

    questions_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioQuestionsTable")
    assert questions_table.item(0, 4).text() == "Why are you applying to Launch Pad Learning now?"
    assert window.admin_draft.overrides["custom_questions"]["preschool"][0]["text"] == "Why are you applying to Launch Pad Learning now?"
    window.window.close()
    app.processEvents()

def test_pyside_admin_questions_track_tabs_switch_visible_flow(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["tracks"]["infant_toddler"] = {"label": "Infant/Toddler", "max_weighted_total": 5}
    for key, label in (
        ("behavior_support", "Behavior Support"),
        ("assistant_director", "Assistant Director"),
        ("school_age", "School Age"),
        ("floaters", "Floaters"),
    ):
        rubric_payload["tracks"][key] = {"label": label, "max_weighted_total": 5}
    rubric_payload["traits"].append(
        {
            "id": "infant_trait_1",
            "name": "Infant Care",
            "priority": "Critical",
            "weight": 1,
            "applicable_tracks": ["infant_toddler"],
            "primary_question": "Tell me about infant routines.",
            "descriptors": {"1": "Concern", "2": "Weak", "3": "Mixed", "4": "Strong", "5": "Excellent"},
            "sample_answers": {},
        }
    )
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = tmp_path / "question_overrides.json"
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {
                    "preschool": [{"id": "Why-LPL", "text": "Why Launch Pad Learning?", "order": 1}],
                    "infant_toddler": [{"id": "Infant-Intro", "text": "Why infant and toddler care?", "order": 1}],
                    "behavior_support": [{"id": "Behavior-Intro", "text": "Why behavior support?", "order": 1}],
                    "assistant_director": [{"id": "Director-Intro", "text": "Why assistant director?", "order": 1}],
                    "school_age": [{"id": "SchoolAge-Intro", "text": "Why school age?", "order": 1}],
                    "floaters": [{"id": "Floater-Intro", "text": "Why floater work?", "order": 1}],
                },
                "track_question_flow": {
                    "preschool": [{"type": "custom", "id": "Why-LPL"}, {"type": "trait", "id": "trait_1"}],
                    "infant_toddler": [
                        {"type": "custom", "id": "Infant-Intro"},
                        {"type": "trait", "id": "infant_trait_1"},
                    ],
                    "behavior_support": [{"type": "custom", "id": "Behavior-Intro"}],
                    "assistant_director": [{"type": "custom", "id": "Director-Intro"}],
                    "school_age": [{"type": "custom", "id": "SchoolAge-Intro"}],
                    "floaters": [{"type": "custom", "id": "Floater-Intro"}],
                },
            }
        ),
        encoding="utf-8",
    )
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(2)

    preschool_tab = window.window.findChild(qt_widgets.QPushButton, "AdminStudioTrackTab_preschool")
    infant_tab = window.window.findChild(qt_widgets.QPushButton, "AdminStudioTrackTab_infant_toddler")
    stack = window.window.findChild(qt_widgets.QStackedWidget, "AdminStudioQuestionFlowStack")
    drawer_id = window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerId")

    assert preschool_tab is not None
    assert infant_tab is not None
    assert stack is not None
    assert preschool_tab.isChecked() is True
    assert infant_tab.isChecked() is False
    track_tabs = window.window.findChildren(qt_widgets.QPushButton)
    track_tabs = [button for button in track_tabs if button.objectName().startswith("AdminStudioTrackTab_")]
    assert len(track_tabs) == 6
    assert {button.property("adminTrackTabRow") for button in track_tabs} == {0, 1}
    assert max(button.property("adminTrackTabRow") for button in track_tabs) == 1
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionCardButton_Why_LPL") is not None
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionCardButton_Infant_Intro") is not None
    prompt_label = window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionPrompt_Why_LPL")
    move_down = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionMoveDown_Why_LPL")
    duplicate = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionDuplicate_Why_LPL")
    more = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionMore_Why_LPL")
    assert prompt_label is not None
    assert prompt_label.minimumWidth() >= 260
    assert prompt_label.sizePolicy().horizontalPolicy() == qt_widgets.QSizePolicy.Policy.Expanding
    assert move_down is not None
    assert move_down.text() == "↓"
    assert move_down.maximumWidth() <= 44
    assert duplicate is not None
    assert duplicate.maximumWidth() <= 44
    assert more is not None
    assert more.maximumWidth() <= 44

    infant_tab.click()

    assert preschool_tab.isChecked() is False
    assert infant_tab.isChecked() is True
    assert stack.currentWidget().property("adminTrackKey") == "infant_toddler"
    assert drawer_id.text() == "Infant-Intro"

    window.window.close()
    app.processEvents()

def test_pyside_admin_add_question_dropzone_opens_blank_drawer_and_saves(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(2)
    window.admin_edit_button.click()

    add_question = window.window.findChild(qt_widgets.QPushButton, "AdminStudioAddQuestionDropZone")
    add_question.click()

    drawer_id = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioQuestionDrawerNewId")
    drawer_label = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioQuestionDrawerNewLabel")
    drawer_section = window.window.findChild(qt_widgets.QComboBox, "AdminStudioQuestionDrawerNewSection")
    drawer_position = window.window.findChild(qt_widgets.QSpinBox, "AdminStudioQuestionDrawerNewPosition")
    drawer_text = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioQuestionDrawerText")
    drawer_save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionDrawerSave")

    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerId").text() == "New question"
    assert drawer_id is not None
    assert drawer_id.isHidden() is False
    assert drawer_section.currentText() == "Qualification"
    assert drawer_position.value() == 2
    drawer_id.setText("classroom_scenario")
    drawer_label.setText("Classroom Scenario")
    drawer_text.setPlainText("How would you respond during a classroom transition?")
    drawer_save.click()

    saved = window.admin_draft.overrides["custom_questions"]["preschool"][1]
    assert saved["id"] == "classroom_scenario"
    assert saved["label"] == "Classroom Scenario"
    assert saved["section"] == "Qualification"
    assert window.admin_draft.overrides["track_question_flow"]["preschool"][1] == {"type": "custom", "id": "classroom_scenario"}
    assert "Unsaved changes:" in window.admin_unsaved_pill.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_question_reorder_updates_draft_flow_without_changing_ids(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(2)
    window.admin_edit_button.click()

    move_down = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionMoveDown_Why_LPL")
    assert move_down is not None
    move_down.click()

    assert window.admin_draft.overrides["track_question_flow"]["preschool"] == [
        {"type": "trait", "id": "trait_1"},
        {"type": "custom", "id": "Why-LPL"},
    ]
    assert window.admin_draft.overrides["custom_questions"]["preschool"][0]["id"] == "Why-LPL"
    assert window.admin_unsaved_pill.text() == "Unsaved changes: 1"
    window.window.close()
    app.processEvents()

def test_pyside_admin_question_drawer_delete_removes_custom_question_from_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(2)
    window.admin_edit_button.click()

    delete_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionDrawerDelete")
    assert delete_button is not None
    assert delete_button.isEnabled() is True
    delete_button.click()

    assert window.admin_draft.overrides["track_question_flow"]["preschool"] == [{"type": "trait", "id": "trait_1"}]
    assert window.admin_draft.overrides["custom_questions"]["preschool"] == []
    app.processEvents()
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionCardButton_Why_LPL") is None
    assert window.admin_unsaved_pill.text() == "Unsaved changes: 1"
    window.window.close()
    app.processEvents()

def test_pyside_admin_question_duplicate_creates_custom_copy_in_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(2)

    duplicate = window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionDuplicate_Why_LPL")
    assert duplicate is not None
    assert duplicate.isEnabled() is False
    window.admin_edit_button.click()
    assert duplicate.isEnabled() is True
    duplicate.click()

    assert window.admin_draft.overrides["track_question_flow"]["preschool"] == [
        {"type": "custom", "id": "Why-LPL"},
        {"type": "custom", "id": "Why-LPL-copy"},
        {"type": "trait", "id": "trait_1"},
    ]
    assert window.admin_draft.overrides["custom_questions"]["preschool"][1]["text"] == "Why Launch Pad Learning?"
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioQuestionCardButton_Why_LPL_copy") is not None
    assert window.admin_unsaved_pill.text() == "Unsaved changes: 1"
    window.window.close()
    app.processEvents()

def test_pyside_admin_layout_keeps_controls_readable_on_narrow_windows(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    toolbar_scroll = window.window.findChild(qt_widgets.QScrollArea, "AdminStudioToolbarScroll")

    window.window.resize(760, 620)
    app.processEvents()

    assert toolbar_scroll is not None
    assert toolbar_scroll.horizontalScrollBarPolicy() == window.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
    sidebar_text_width = max(
        window.sidebar.fontMetrics().horizontalAdvance(window.sidebar.item(row).text())
        for row in range(window.sidebar.count())
    )
    assert window.sidebar_panel.maximumWidth() >= sidebar_text_width + 44
    assert window.sidebar_panel.maximumWidth() <= 260
    nav_text_width = max(
        section_list.fontMetrics().horizontalAdvance(section_list.item(row).text())
        for row in range(section_list.count())
        if section_list.item(row).data(window.QtCore.Qt.ItemDataRole.UserRole) != "group"
    )
    assert section_list.maximumWidth() >= nav_text_width + 56
    assert section_list.horizontalScrollBarPolicy() == window.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert window.admin_status_label.isVisible() is False
    assert section_list.item(2).toolTip() == section_list.item(2).text()
    for button in (
        window.admin_edit_button,
        window.admin_save_draft_button,
        window.admin_review_button,
        window.admin_publish_button,
        window.admin_discard_button,
    ):
        assert button.minimumWidth() >= button.sizeHint().width()
        assert button.toolTip() == button.text()
    window.window.resize(1260, 820)
    app.processEvents()
    assert window.sidebar_panel.maximumWidth() >= 230
    assert section_list.maximumWidth() >= 240
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_admin_layout_uses_font_metrics_for_windows_text_scaling(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    original_font = app.font()
    scaled_font = qt_gui.QFont(original_font)
    scaled_font.setPointSize(max(original_font.pointSize() + 8, 18))
    app.setFont(scaled_font)
    try:
        rubric_path = _write_test_rubric(tmp_path)
        overrides_path = _write_test_overrides(tmp_path)
        settings_path = tmp_path / "school_offer_settings.json"
        settings_path.write_text(json.dumps({}), encoding="utf-8")
        prompts_path = tmp_path / "deepseek_prompts.json"
        prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
        app_settings_path = tmp_path / "interview_app_settings.json"
        app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
        notification_rules_path = tmp_path / "notification_rules.sqlite3"
        monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
        monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
        monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
        monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
        monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
        model = build_interview_redesign_model(
            rubric_path=rubric_path,
            overrides_path=overrides_path,
            history_path=tmp_path / "missing-history.json",
            school_options=["Palmdale"],
        )
        window = _pyside_window_on_page(model, "Admin")
        section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")

        window.window.resize(760, 620)
        app.processEvents()

        publish = window.admin_publish_button
        publish_text_width = publish.fontMetrics().horizontalAdvance(publish.text())
        assert publish.minimumWidth() >= publish_text_width + 32
        nav_text_width = max(
            section_list.fontMetrics().horizontalAdvance(section_list.item(row).text())
            for row in range(section_list.count())
            if section_list.item(row).data(window.QtCore.Qt.ItemDataRole.UserRole) != "group"
        )
        assert section_list.minimumWidth() >= nav_text_width + 56
        assert section_list.maximumWidth() >= section_list.minimumWidth()
        assert window.admin_sidebar_rail.minimumWidth() >= section_list.minimumWidth()
        window.window.close()
        app.processEvents()
    finally:
        app.setFont(original_font)


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_initial_window_fits_available_screen_after_display_scaling(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    available = app.primaryScreen().availableGeometry()

    assert window.window.width() <= max(640, available.width() - 40)
    assert window.window.height() <= max(480, available.height() - 40)
    assert window.window.maximumWidth() == 16777215
    assert window.window.maximumHeight() == 16777215
    assert window.window.isMaximized() is False
    legacy_label = "Switch to " + "Tk UI"
    assert all(button.text() != legacy_label for button in window.window.findChildren(qt_widgets.QPushButton))
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_admin_rubrics_editor_matches_mockup_and_saves_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["tracks"]["infant_toddler"] = {"label": "Infant/Toddler", "max_weighted_total": 5}
    rubric_payload["traits"][0]["sample_answers"] = {
        "1": "Concern sample",
        "2": "Weak sample",
        "3": "Mixed sample",
        "4": "Strong sample",
        "5": "Excellent sample",
    }
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(3)

    trait_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioTraitCardButton_trait_1")
    editor_title = window.window.findChild(qt_widgets.QLabel, "AdminStudioRubricEditorTitle")
    tabs = window.window.findChild(qt_widgets.QTabWidget, "AdminStudioRubricEditorTabs")
    trait_id = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioRubricTraitId")
    trait_name = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioRubricTraitName")
    weight = window.window.findChild(qt_widgets.QSpinBox, "AdminStudioRubricTraitWeight")
    weight_minus = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricWeightMinus")
    weight_plus = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricWeightPlus")
    primary_question = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioRubricPrimaryQuestion")
    descriptor_5 = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioRubricDescriptorText_5")
    sample_5 = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioRubricSampleAnswerText_5")
    sample_1 = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioRubricSampleAnswerText_1")
    preschool_track = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioRubricApplicableTrack_preschool")
    infant_track = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioRubricApplicableTrack_infant_toddler")
    delete_trait = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricDeleteTrait")
    duplicate = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricDuplicateTrait")
    save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricSaveChanges")

    assert trait_button is not None
    assert editor_title.text() == "Edit Rubric Trait"
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Overview",
        "Score Descriptors",
        "Sample Answers",
        "Publish Rules",
    ]
    assert trait_id.text() == "trait_1"
    assert trait_name.text() == "Empathy"
    assert weight.value() == 1
    assert weight_minus is not None
    assert weight_minus.isEnabled() is False
    assert weight_plus is not None
    assert weight_plus.isEnabled() is False
    assert primary_question.toPlainText() == "Tell me about a hard child moment."
    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioRubricDescriptorRow")) == 5
    assert descriptor_5 is not None
    assert descriptor_5.toPlainText() == "Excellent"
    assert descriptor_5.isEnabled() is False
    assert preschool_track is not None
    assert preschool_track.isChecked() is True
    assert preschool_track.isEnabled() is False
    assert infant_track is not None
    assert infant_track.isChecked() is False
    assert infant_track.isEnabled() is False
    sample_rows = window.window.findChildren(qt_widgets.QFrame, "AdminStudioRubricSampleAnswerRow")
    assert len(sample_rows) == 5
    assert sample_5 is not None
    assert sample_5.toPlainText() == "Excellent sample"
    assert sample_5.isEnabled() is False
    assert sample_1 is not None
    assert sample_1.toPlainText() == "Concern sample"
    publish_rule_rows = window.window.findChildren(qt_widgets.QFrame, "AdminStudioRubricPublishRuleRow")
    assert len(publish_rule_rows) >= 3
    assert "Automatic no-hire" in _widget_text(publish_rule_rows[0])
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioRubricValidationImpactPanel") is not None
    linked_question_panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioRubricLinkedQuestionPanel")
    assert linked_question_panel is not None
    linked_open = linked_question_panel.findChild(qt_widgets.QPushButton, "AdminStudioRubricLinkedQuestionOpen")
    assert linked_open is not None
    assert trait_name.isEnabled() is False
    assert delete_trait is not None
    assert delete_trait.isEnabled() is False
    assert duplicate is not None
    assert duplicate.isEnabled() is False
    assert save.isEnabled() is False

    linked_open.click()
    assert section_list.currentItem().text() == "Questions & Flow"
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerId").text() == "trait_1"
    section_list.setCurrentRow(3)

    window.admin_edit_button.click()
    trait_button.click()
    assert trait_name.isEnabled() is True
    assert descriptor_5.isEnabled() is True
    assert sample_5.isEnabled() is True
    assert infant_track.isEnabled() is True
    assert weight_minus.isEnabled() is True
    assert weight_plus.isEnabled() is True
    trait_name.setText("Empathy & Respect for Children")
    weight_plus.click()
    weight_plus.click()
    assert weight.value() == 3
    infant_track.setChecked(True)
    descriptor_5.setPlainText("Exceptional empathy, validation, and child-centered response.")
    sample_5.setPlainText("Candidate gives a specific, child-centered repair example.")
    save.click()

    assert window.admin_draft.rubric["traits"][0]["name"] == "Empathy & Respect for Children"
    assert window.admin_draft.rubric["traits"][0]["weight"] == "3"
    assert window.admin_draft.rubric["traits"][0]["applicable_tracks"] == ["preschool", "infant_toddler"]
    assert window.admin_draft.rubric["traits"][0]["descriptors"]["5"] == "Exceptional empathy, validation, and child-centered response."
    assert window.admin_draft.rubric["traits"][0]["sample_answers"]["5"] == "Candidate gives a specific, child-centered repair example."
    duplicate.click()
    app.processEvents()

    assert [trait["id"] for trait in window.admin_draft.rubric["traits"]] == ["trait_1", "trait_2"]
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioTraitCardButton_trait_2") is not None
    rubrics_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioRubricsTable")
    assert rubrics_table.item(0, 1).text() == "Empathy & Respect for Children"
    assert rubrics_table.item(1, 0).text() == "trait_2"
    delete_trait = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricDeleteTrait")
    delete_trait.click()
    app.processEvents()

    assert [trait["id"] for trait in window.admin_draft.rubric["traits"]] == ["trait_1"]
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioTraitCardButton_trait_2") is None
    assert rubrics_table.rowCount() == 1
    window.admin_save_draft_button.click()
    window.window.close()
    app.processEvents()

def test_pyside_admin_rubrics_linked_question_follows_selected_trait(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["traits"][0]["weight"] = 3
    rubric_payload["traits"].append(
        {
            "id": "trait_2",
            "name": "Coachability",
            "priority": "High",
            "weight": 2,
            "applicable_tracks": ["preschool"],
            "primary_question": "Tell me about feedback you received.",
            "descriptors": {"1": "Concern", "2": "Weak", "3": "Mixed", "4": "Strong", "5": "Excellent"},
            "sample_answers": {},
        }
    )
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    overrides_payload = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides_payload["track_question_flow"]["preschool"].append({"type": "trait", "id": "trait_2"})
    overrides_path.write_text(json.dumps(overrides_payload), encoding="utf-8")
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(3)

    trait_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioTraitCardButton_trait_2")
    linked_preview = window.window.findChild(qt_widgets.QLabel, "AdminStudioRubricLinkedQuestionPreview")
    linked_open = window.window.findChild(qt_widgets.QPushButton, "AdminStudioRubricLinkedQuestionOpen")

    assert trait_button is not None
    trait_button.click()
    assert linked_preview is not None
    assert linked_preview.text() == "Tell me about feedback you received."
    linked_open.click()

    assert section_list.currentItem().text() == "Questions & Flow"
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioQuestionDrawerId").text() == "trait_2"
    window.window.close()
    app.processEvents()

def test_pyside_admin_rubrics_renders_all_trait_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["traits"] = [
        {
            "id": f"trait_{index}",
            "name": f"Trait {index}",
            "priority": "Medium",
            "weight": 1,
            "applicable_tracks": ["preschool"],
            "primary_question": f"Question {index}",
            "descriptors": {"1": "Concern", "2": "Weak", "3": "Mixed", "4": "Strong", "5": "Excellent"},
            "sample_answers": {},
        }
        for index in range(1, 11)
    ]
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(3)

    cards = window.window.findChildren(qt_widgets.QFrame, "AdminStudioTraitCard_trait_10")
    trait_10 = window.window.findChild(qt_widgets.QPushButton, "AdminStudioTraitCardButton_trait_10")

    assert len(window.window.findChildren(qt_widgets.QFrame, "AdminStudioTraitCard_trait_1")) == 1
    assert "Total Traits\n10" in _widget_text(window.window.findChild(qt_widgets.QFrame, "AdminStudioRubricTraitCardsPanel"))
    assert cards
    assert trait_10 is not None
    trait_10.click()
    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioRubricTraitId").text() == "trait_10"
    window.window.close()
    app.processEvents()

def test_pyside_admin_rubrics_filters_search_and_view_controls_trait_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["traits"][0]["weight"] = 3
    rubric_payload["traits"].append(
        {
            "id": "trait_2",
            "name": "Coachability",
            "priority": "High",
            "weight": 2,
            "applicable_tracks": ["preschool"],
            "primary_question": "Tell me about feedback you received.",
            "descriptors": {"1": "Concern", "2": "Weak", "3": "Mixed", "4": "Strong", "5": "Excellent"},
            "sample_answers": {},
        }
    )
    rubric_payload["traits"].append(
        {
            "id": "trait_3",
            "name": "Unlinked Trait",
            "priority": "Medium",
            "weight": 1,
            "applicable_tracks": ["preschool"],
            "primary_question": "No linked question configured.",
            "descriptors": {"1": "Concern", "2": "Weak", "3": "Mixed", "4": "Strong", "5": "Excellent"},
            "sample_answers": {},
        }
    )
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(3)

    search = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioRubricSearchInput")
    priority = window.window.findChild(qt_widgets.QComboBox, "AdminStudioRubricPriorityFilter")
    weight = window.window.findChild(qt_widgets.QComboBox, "AdminStudioRubricWeightFilter")
    linked = window.window.findChild(qt_widgets.QComboBox, "AdminStudioRubricLinkedQuestionFilter")
    view_toggle = window.window.findChild(qt_widgets.QComboBox, "AdminStudioRubricViewToggle")
    empathy_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioTraitCard_trait_1")
    coachability_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioTraitCard_trait_2")
    unlinked_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioTraitCard_trait_3")

    assert search is not None
    assert search.placeholderText() == "Search traits..."
    assert priority is not None
    assert [priority.itemText(index) for index in range(priority.count())] == ["All priorities", "Critical", "High", "Medium", "Low"]
    assert weight is not None
    assert [weight.itemText(index) for index in range(weight.count())] == ["All weights", "Weight 3+", "Weight 2", "Weight 1"]
    assert linked is not None
    assert [linked.itemText(index) for index in range(linked.count())] == ["All linked states", "Has linked question", "Missing linked question"]
    assert view_toggle is not None
    assert [view_toggle.itemText(index) for index in range(view_toggle.count())] == ["Grid view", "List view"]
    assert empathy_card is not None
    assert coachability_card is not None
    assert unlinked_card is not None

    search.setText("coach")
    app.processEvents()
    assert empathy_card.property("adminRubricFilterMatch") is False
    assert coachability_card.property("adminRubricFilterMatch") is True
    assert empathy_card.isHidden() is True
    assert coachability_card.isHidden() is False

    search.clear()
    priority.setCurrentText("Critical")
    app.processEvents()
    assert empathy_card.property("adminRubricFilterMatch") is True
    assert coachability_card.property("adminRubricFilterMatch") is False
    priority.setCurrentText("All priorities")
    weight.setCurrentText("Weight 2")
    app.processEvents()
    assert empathy_card.property("adminRubricFilterMatch") is False
    assert coachability_card.property("adminRubricFilterMatch") is True
    weight.setCurrentText("Weight 3+")
    app.processEvents()
    assert empathy_card.property("adminRubricFilterMatch") is True
    assert coachability_card.property("adminRubricFilterMatch") is False
    assert unlinked_card.property("adminRubricFilterMatch") is False
    weight.setCurrentText("All weights")
    linked.setCurrentText("Missing linked question")
    app.processEvents()
    assert empathy_card.property("adminRubricFilterMatch") is False
    assert coachability_card.property("adminRubricFilterMatch") is False
    assert unlinked_card.property("adminRubricFilterMatch") is True
    linked.setCurrentText("Has linked question")
    app.processEvents()
    assert empathy_card.property("adminRubricFilterMatch") is True
    assert coachability_card.property("adminRubricFilterMatch") is True
    assert unlinked_card.property("adminRubricFilterMatch") is False
    assert view_toggle.currentText() == "Grid view"
    view_toggle.setCurrentText("List view")
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioRubricTraitCardsPanel").property("adminRubricViewMode") == "list"
    window.window.close()
    app.processEvents()

def test_pyside_admin_signal_hints_search_and_detail_reference(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["traits"][0].update(
        {
            "description": "Shows warmth, respect, and empathy toward children.",
            "signal_hints": [
                "Validates feelings",
                "Uses calm child-centered language",
                "Names what the child may need",
            ],
            "usage_notes": "Look for real examples of past child interactions.",
        }
    )
    rubric_payload["traits"].append(
        {
            "id": "trait_2",
            "name": "Coachability",
            "priority": "High",
            "weight": 2,
            "applicable_tracks": ["preschool"],
            "primary_question": "Tell me about feedback you received.",
            "description": "Open to feedback and committed to growth.",
            "signal_hints": ["Accepts feedback", "Changes practice", "Reflects without defensiveness"],
            "usage_notes": "Strong candidates describe what changed after feedback.",
            "descriptors": {
                "1": "Rejects feedback",
                "2": "Defensive",
                "3": "Accepts feedback",
                "4": "Applies feedback",
                "5": "Seeks feedback and improves",
            },
            "sample_answers": {
                "1": "I ignore feedback.",
                "2": "I get defensive.",
                "3": "I listen.",
                "4": "I changed my classroom routine.",
                "5": "I ask for feedback and follow up.",
            },
        }
    )
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(4)

    search = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioSignalSearchInput")
    summary_strip = window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalSummaryStrip")
    all_category = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSignalCategory_All")
    fixed_categories = {
        name: window.window.findChild(qt_widgets.QPushButton, f"AdminStudioSignalCategory_{name}")
        for name in ("Empathy", "Regulation", "Accountability", "Guidance", "Teamwork", "Communication", "Structure", "Other")
    }
    coach_category = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSignalCategory_Coachability")
    empathy_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSignalHintButton_trait_1")
    coach_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSignalHintButton_trait_2")
    title = window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalDetailTitle")
    definition = window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalDefinitionText")
    scoring = window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalScoringMeaningText")
    phrases = window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalExamplePhrases")
    metadata = window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalFooterMetadata")

    assert search is not None
    assert search.placeholderText() == "Search signal hints by trait, keyword, or phrase..."
    assert summary_strip is not None
    assert "2 hint groups" in _widget_text(summary_strip)
    assert "Hint Groups (2)" in _widget_text(window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalHintListPanel"))
    for name, button in fixed_categories.items():
        assert button is not None, name
        assert button.property("adminSignalCategory") == name
    assert all_category is not None
    assert all_category.property("adminSignalCategorySelected") is True
    assert coach_category is not None
    assert empathy_button is not None
    assert coach_button is not None
    assert empathy_button.property("adminSignalSelected") is True
    assert coach_button.property("adminSignalSelected") is False
    assert title.text() == "Empathy"
    assert "warmth, respect, and empathy" in definition.text()
    high_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalLevelHighCard")
    moderate_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalLevelModerateCard")
    low_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalLevelLowCard")
    assert high_card is not None
    assert moderate_card is not None
    assert low_card is not None
    assert high_card.property("adminSignalLevelTone") == "high"
    assert moderate_card.property("adminSignalLevelTone") == "moderate"
    assert low_card.property("adminSignalLevelTone") == "low"
    assert "#dcfce7" in high_card.styleSheet()
    assert "#fef9c3" in moderate_card.styleSheet()
    assert "#fee2e2" in low_card.styleSheet()
    assert metadata is not None
    assert "Category: Empathy" in metadata.text()
    assert "Status: Up to date" in metadata.text()

    coach_category.click()
    app.processEvents()
    assert coach_category.property("adminSignalCategorySelected") is True
    assert all_category.property("adminSignalCategorySelected") is False
    assert empathy_button.property("adminSignalSearchMatch") is False
    assert coach_button.property("adminSignalSearchMatch") is True

    all_category.click()
    app.processEvents()
    assert empathy_button.property("adminSignalSearchMatch") is True

    search.setText("coach")
    app.processEvents()
    assert coach_button.property("adminSignalSearchMatch") is True
    coach_button.click()

    assert title.text() == "Coachability"
    assert empathy_button.property("adminSignalSelected") is False
    assert coach_button.property("adminSignalSelected") is True
    assert "Open to feedback" in definition.text()
    assert "Higher scores indicate" in scoring.text()
    assert "I ask for feedback" in phrases.text()
    assert "Category: Guidance" in metadata.text()
    assert "Last updated:" in metadata.text()
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalUsageNotes").text().startswith("Strong candidates")
    window.window.close()
    app.processEvents()

def test_pyside_admin_signal_hints_uses_cards_without_legacy_table(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(4)

    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalHintListPanel") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalDetailPanel") is not None
    assert window.window.findChild(qt_widgets.QTableWidget, "AdminStudioSignalsTable") is None
    window.window.close()
    app.processEvents()

def test_pyside_admin_signal_hints_renders_all_hint_groups(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric_payload["traits"] = [
        {
            "id": f"trait_{index}",
            "name": f"Trait {index}",
            "priority": "Medium",
            "weight": 1,
            "applicable_tracks": ["preschool"],
            "primary_question": f"Question {index}",
            "description": f"Signal definition for trait {index}.",
            "signal_hints": [f"Signal {index}A", f"Signal {index}B"],
            "usage_notes": f"Usage notes {index}.",
            "descriptors": {"1": "Low", "3": "Moderate", "5": "High"},
            "sample_answers": {"5": f"Strong example {index}."},
        }
        for index in range(1, 11)
    ]
    rubric_path.write_text(json.dumps(rubric_payload), encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(4)

    cards = window.window.findChildren(qt_widgets.QFrame, "AdminStudioSignalHintGroup")
    trait_10 = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSignalHintButton_trait_10")

    assert len(cards) == 10
    assert "10 hint groups" in _widget_text(window.window.findChild(qt_widgets.QFrame, "AdminStudioSignalSummaryStrip"))
    assert trait_10 is not None
    trait_10.click()
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSignalDetailTitle").text() == "Trait 10"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notification_rule_editor_saves_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    saved_rule = NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now: {position_name}",
            body_template="Hi {hiring_manager_name}, please review {position_name}.",
            recipients=[NotificationRecipient(email="director@example.org", role_label="Director")],
            trigger_timing="event",
            offset_days=0,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    rule_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_staffing_assign_manager")
    rule_cards = [
        card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
        if card.property("adminNotificationEvent") == "staffing.assign-manager"
    ]
    open_rule = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationOpenRule_staffing_assign_manager")
    label = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleLabel")
    event = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent")
    active = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioNotificationRuleActive")
    subject = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleSubject")
    body = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioNotificationRuleBody")
    recipients = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleRecipients")
    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioNotificationRuleValidation")
    position_variable = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationVariable_position_name")
    cancel = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleCancel")
    save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave")

    assert rule_button is not None
    assert rule_cards
    assert f"ID: {saved_rule.id}" in _widget_text(rule_cards[0])
    assert open_rule is not None
    open_rule.click()
    assert event.text() == "staffing.assign-manager"
    rule_button.click()
    assert label.text() == "Hiring manager: position needed now"
    assert event.text() == "staffing.assign-manager"
    assert active.isChecked() is True
    assert subject.text() == "Position needed now: {position_name}"
    assert "{hiring_manager_name}" in body.toPlainText()
    assert "director@example.org" in recipients.text()
    assert position_variable is not None
    assert validation.text() == "No issues found. Subject and body templates look good."
    assert label.isEnabled() is False
    assert save.isEnabled() is False

    window.admin_edit_button.click()
    rule_button.click()
    assert label.isEnabled() is True
    assert cancel is not None
    subject.setText("Temporary unsaved subject")
    label.setText("Temporary label")
    cancel.click()
    assert label.text() == "Hiring manager: position needed now"
    assert subject.text() == "Position needed now: {position_name}"
    subject.clear()
    app.processEvents()
    assert "Missing subject template." in validation.text()
    label.setText("Hiring manager: urgent position")
    subject.setText("Urgent: {position_name}")
    app.processEvents()
    assert "Missing subject template." not in validation.text()
    body.setPlainText("Hi {hiring_manager_name}, urgent review needed for .")
    cursor = body.textCursor()
    cursor.setPosition(body.toPlainText().index("for ") + len("for "))
    body.setTextCursor(cursor)
    position_variable.click()
    assert body.toPlainText() == "Hi {hiring_manager_name}, urgent review needed for {position_name}."
    recipients.setText("director@example.org, owner@example.org")
    save.click()
    window.admin_save_draft_button.click()

    saved = next(rule for rule in window.admin_draft.notification_rules if rule.event_type == "staffing.assign-manager")
    assert saved.label == "Hiring manager: urgent position"
    assert saved.subject_template == "Urgent: {position_name}"
    assert len(saved.recipients) == 2
    notifications_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioNotificationsTable")
    saved_row = next(
        row
        for row in range(notifications_table.rowCount())
        if notifications_table.item(row, 1) and notifications_table.item(row, 1).text() == "staffing.assign-manager"
    )
    assert notifications_table.item(saved_row, 2).text() == "Hiring manager: urgent position"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_create_button_opens_blank_rule_and_saves_card(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now: {position_name}",
            body_template="Hi {hiring_manager_name}, please review {position_name}.",
            recipients=[NotificationRecipient(email="director@example.org", role_label="Director")],
            trigger_timing="event",
            offset_days=0,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    create = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationCreateTemplateButton")
    event = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent")
    label = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleLabel")
    active = window.window.findChild(qt_widgets.QCheckBox, "AdminStudioNotificationRuleActive")
    subject = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleSubject")
    body = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioNotificationRuleBody")
    recipients = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleRecipients")
    save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave")

    assert create is not None
    assert create.isEnabled() is False
    window.admin_edit_button.click()
    create.click()

    assert event.text() == ""
    assert label.text() == ""
    assert active.isChecked() is False
    event.setText("offer.generated.followup")
    label.setText("Offer generated follow-up")
    active.setChecked(True)
    subject.setText("Offer generated: {candidate_name}")
    body.setPlainText("Follow up on {candidate_name}.")
    recipients.setText("director@example.org")
    save.click()

    saved = next(rule for rule in window.admin_draft.notification_rules if rule.event_type == "offer.generated.followup")
    assert saved.label == "Offer generated follow-up"
    assert saved.active is True
    assert saved.subject_template == "Offer generated: {candidate_name}"
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_offer_generated_followup") is not None
    notifications_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioNotificationsTable")
    assert any(
        notifications_table.item(row, 1) and notifications_table.item(row, 1).text() == "offer.generated.followup"
        for row in range(notifications_table.rowCount())
    )
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_editor_shows_mockup_status_panels(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now: {position_name}",
            body_template="Hi {hiring_manager_name}, review {position_name} in {department}.",
            recipients=[
                NotificationRecipient(email="manager@example.org", role_label="Hiring Manager"),
                NotificationRecipient(email="director@example.org", role_label="Director"),
            ],
            trigger_timing="event",
            offset_days=0,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    rule_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_staffing_assign_manager")
    rule_button.click()

    selected_card = next(
        card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
        if card.property("adminNotificationEvent") == "staffing.assign-manager"
    )
    editor_meta = window.window.findChild(qt_widgets.QLabel, "AdminStudioNotificationEditorMeta")
    variables_panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationVariablesPreviewPanel")
    validation_panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationValidationPanel")

    assert selected_card is not None
    assert selected_card.property("adminNotificationSelected") is True
    assert editor_meta is not None
    assert "ID:" in editor_meta.text()
    assert "staffing.assign-manager" in editor_meta.text()
    assert variables_panel is not None
    variables_text = _widget_text(variables_panel)
    assert "Variables Preview" in variables_text
    assert "{hiring_manager_name}" in variables_text
    assert "{department}" in variables_text
    assert validation_panel is not None
    validation_text = _widget_text(validation_panel)
    assert "Validation" in validation_text
    assert "No issues found" in validation_text
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_admin_notifications_recipient_chips_remove_and_save(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now: {position_name}",
            body_template="Hi {hiring_manager_name}, review {position_name}.",
            recipients=[
                NotificationRecipient(email="manager@example.org", role_label="Hiring Manager"),
                NotificationRecipient(email="director@example.org", role_label="Director"),
            ],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_staffing_assign_manager").click()

    chips_panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationRecipientChips")
    remove_director = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRecipientRemove_director_example_org")

    assert chips_panel is not None
    chip_text = _widget_text(chips_panel)
    assert "Hiring Manager" in chip_text
    assert "manager@example.org" in chip_text
    assert "Director" in chip_text
    assert "director@example.org" in chip_text
    assert remove_director is not None
    assert remove_director.isEnabled() is False

    window.admin_edit_button.click()
    assert remove_director.isEnabled() is True
    remove_director.click()

    recipients = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleRecipients")
    assert recipients.text() == "manager@example.org"
    assert "director@example.org" not in _widget_text(chips_panel)

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave").click()
    saved = next(rule for rule in window.admin_draft.notification_rules if rule.event_type == "staffing.assign-manager")
    assert [recipient.email for recipient in saved.recipients] == ["manager@example.org"]
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_body_toolbar_inserts_template_markup(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now: {position_name}",
            body_template="Hi team,",
            recipients=[NotificationRecipient(email="manager@example.org", role_label="Hiring Manager")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_staffing_assign_manager").click()

    toolbar = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationBodyToolbar")
    bold = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationBodyBold")
    italic = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationBodyItalic")
    bullets = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationBodyBullets")
    variables = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationBodyVariables")
    body = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioNotificationRuleBody")

    assert toolbar is not None
    assert bold is not None
    assert italic is not None
    assert bullets is not None
    assert variables is not None
    assert bold.isEnabled() is False

    window.admin_edit_button.click()
    assert bold.isEnabled() is True
    body.moveCursor(window.QtGui.QTextCursor.MoveOperation.End)
    bold.click()
    italic.click()
    bullets.click()
    variables.click()

    text = body.toPlainText()
    assert "**bold text**" in text
    assert "_italic text_" in text
    assert "- list item" in text
    assert "{position_name}" in text

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave").click()
    saved = next(rule for rule in window.admin_draft.notification_rules if rule.event_type == "staffing.assign-manager")
    assert "**bold text**" in saved.body_template
    assert "{position_name}" in saved.body_template
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_subject_variable_button_inserts_and_saves(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now:",
            body_template="Hi team {position_name}.",
            recipients=[NotificationRecipient(email="manager@example.org", role_label="Hiring Manager")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_staffing_assign_manager").click()

    tools = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationSubjectTools")
    variable = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationSubjectVariable_position_name")
    subject = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleSubject")

    assert tools is not None
    assert variable is not None
    assert subject is not None
    assert variable.isEnabled() is False

    window.admin_edit_button.click()
    subject.setCursorPosition(len(subject.text()))
    variable.click()

    assert subject.text() == "Position needed now:{position_name}"
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave").click()
    saved = next(rule for rule in window.admin_draft.notification_rules if rule.event_type == "staffing.assign-manager")
    assert saved.subject_template == "Position needed now:{position_name}"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_date_offset_uses_clear_before_after_controls(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="onboarding.start-reminder",
            label="Start reminder",
            active=True,
            subject_template="Start soon: {person_name}",
            body_template="{person_name} starts on {start_date}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="date_offset",
            date_field="start_date",
            offset_days=-3,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_onboarding_start_reminder").click()

    timing = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationRuleTiming")
    date_field = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationDateField")
    direction = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationOffsetDirection")
    days = window.window.findChild(qt_widgets.QSpinBox, "AdminStudioNotificationRuleOffsetDays")
    summary = window.window.findChild(qt_widgets.QLabel, "AdminStudioNotificationScheduleSummary")

    assert timing.currentText() == "Before/on/after a reference date"
    assert timing.currentData(window.QtCore.Qt.ItemDataRole.UserRole) == "date_offset"
    assert date_field is not None
    assert date_field.currentText() == "Employee start date"
    assert date_field.currentData(window.QtCore.Qt.ItemDataRole.UserRole) == "start_date"
    assert date_field.isEditable() is True
    date_options = [date_field.itemText(index) for index in range(date_field.count())]
    date_option_keys = [
        date_field.itemData(index, window.QtCore.Qt.ItemDataRole.UserRole)
        for index in range(date_field.count())
    ]
    assert "Date notice given" in date_options
    assert "Last working day" in date_options
    assert "Offer generated date" in date_options
    assert "date_notice_given" in date_option_keys
    assert "last_working_day" in date_option_keys
    assert direction is not None
    assert direction.currentText() == "Before"
    assert days.value() == 3
    assert "3 days before employee start date" in summary.text()
    assert "Offset Days" not in _widget_text(window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationEditPanel"))
    assert "Reference date" in _widget_text(window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationEditPanel"))
    assert direction.isEnabled() is False

    window.admin_edit_button.click()
    timing.setCurrentText("When event happens")
    assert "When event happens" in summary.text()
    timing.setCurrentText("Before/on/after a reference date")
    date_field.setCurrentText("custom_event_date")
    direction.setCurrentText("After")
    days.setValue(5)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleSave").click()

    saved = next(rule for rule in window.admin_draft.notification_rules if rule.event_type == "onboarding.start-reminder")
    assert saved.trigger_timing == "date_offset"
    assert saved.date_field == "custom_event_date"
    assert saved.offset_days == 5
    assert "5 days after custom event date" in summary.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_delete_rule_removes_draft_card_and_table_row(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="Offer accepted",
            active=False,
            subject_template="Accepted: {candidate_name}",
            body_template="{candidate_name} accepted.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="Approved: {candidate_name}",
            body_template="{candidate_name} approved.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    accepted = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_offer_accepted")
    delete_rule = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleDelete")
    table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioNotificationsTable")

    assert accepted is not None
    assert delete_rule is not None
    assert delete_rule.isEnabled() is False

    accepted.click()
    window.admin_edit_button.click()
    accepted.click()
    delete_rule.click()

    assert "offer.accepted" not in [rule.event_type for rule in window.admin_draft.notification_rules]
    remaining_events = [
        card.property("adminNotificationEvent")
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    ]
    assert "offer.accepted" not in remaining_events
    assert "offer.approved" in remaining_events
    table_events = {
        table.item(row, 1).text()
        for row in range(table.rowCount())
        if table.item(row, 1) is not None
    }
    assert "offer.accepted" not in table_events
    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent").text() == "offer.approved"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notification_preview_modal_renders_sample_data(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager: position needed now",
            active=True,
            subject_template="Position needed now: {position_name}",
            body_template="Hi {hiring_manager_name}, review {position_name} for {unknown_token}.",
            recipients=[NotificationRecipient(email="director@example.org", role_label="Director")],
            trigger_timing="event",
            offset_days=0,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_staffing_assign_manager").click()

    preview_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationPreviewButton")
    assert preview_button is not None
    preview_button.click()
    dialog = window.admin_notification_preview_dialog

    assert dialog.objectName() == "AdminStudioNotificationPreviewDialog"
    assert "Notification Template Preview" in dialog.windowTitle()
    assert "Position needed now: Preschool Teacher" in _widget_text(dialog)
    assert "Hi Harper Lee, review Preschool Teacher" in _widget_text(dialog)
    assert "Unresolved variables: unknown_token" in _widget_text(dialog)
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_enabled_filter_hides_nonmatching_rule_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="Approved: {candidate_name}",
            body_template="{candidate_name} approved.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="Offer accepted",
            active=False,
            subject_template="Accepted: {candidate_name}",
            body_template="{candidate_name} accepted.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    status_filter = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationEnabledStatusFilter")
    assert status_filter is not None
    status_filter.setCurrentText("Disabled")
    cards = {
        card.property("adminNotificationEvent"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    }

    assert cards["offer.approved"].isHidden()
    assert not cards["offer.accepted"].isHidden()
    assert cards["offer.approved"].property("adminNotificationFilterMatch") is False
    assert cards["offer.accepted"].property("adminNotificationFilterMatch") is True

    clear = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationClearFilters")
    assert clear is not None
    clear.click()
    assert not cards["offer.approved"].isHidden()
    assert not cards["offer.accepted"].isHidden()
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_timing_filter_hides_nonmatching_rule_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="offer.generated",
            label="Offer generated",
            active=True,
            subject_template="Generated: {candidate_name}",
            body_template="{candidate_name} generated.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="event",
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="onboarding.start-reminder",
            label="Start reminder",
            active=True,
            subject_template="Start soon: {person_name}",
            body_template="{person_name} starts on {start_date}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="date_offset",
            date_field="start_date",
            offset_days=-3,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    timing_filter = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationTimingFilter")
    assert timing_filter is not None
    timing_filter.setCurrentText("Reference date")
    cards = {
        card.property("adminNotificationEvent"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    }

    assert cards["offer.generated"].isHidden()
    assert not cards["onboarding.start-reminder"].isHidden()
    assert cards["offer.generated"].property("adminNotificationFilterMatch") is False
    assert cards["onboarding.start-reminder"].property("adminNotificationFilterMatch") is True

    clear = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationClearFilters")
    clear.click()
    assert timing_filter.currentText() == "All timings"
    assert not cards["offer.generated"].isHidden()
    assert not cards["onboarding.start-reminder"].isHidden()
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_recipients_filter_hides_empty_recipient_rules(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="Approved: {candidate_name}",
            body_template="{candidate_name} approved.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="custom.missing-recipient",
            label="Missing recipient",
            active=True,
            subject_template="Needs recipient",
            body_template="Needs recipient.",
            recipients=[],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    recipients_filter = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationRecipientsFilter")
    assert recipients_filter is not None
    recipients_filter.setCurrentText("No recipients")
    cards = {
        card.property("adminNotificationEvent"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    }

    assert cards["offer.approved"].isHidden()
    assert not cards["custom.missing-recipient"].isHidden()
    assert cards["offer.approved"].property("adminNotificationFilterMatch") is False
    assert cards["custom.missing-recipient"].property("adminNotificationFilterMatch") is True

    recipients_filter.setCurrentText("Has recipients")
    assert not cards["offer.approved"].isHidden()
    assert cards["custom.missing-recipient"].isHidden()

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationClearFilters").click()
    assert recipients_filter.currentText() == "All recipients"
    assert not cards["offer.approved"].isHidden()
    assert not cards["custom.missing-recipient"].isHidden()
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_event_filter_hides_nonmatching_rule_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="offer.generated",
            label="Offer generated",
            active=True,
            subject_template="Generated: {candidate_name}",
            body_template="{candidate_name} generated.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager",
            active=True,
            subject_template="Position: {position_name}",
            body_template="{position_name}",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    event_filter = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationEventFilter")
    assert event_filter is not None
    assert "offer.generated" in [event_filter.itemText(index) for index in range(event_filter.count())]
    event_filter.setCurrentText("offer.generated")
    cards = {
        card.property("adminNotificationEvent"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    }

    assert not cards["offer.generated"].isHidden()
    assert cards["staffing.assign-manager"].isHidden()
    assert cards["offer.generated"].property("adminNotificationFilterMatch") is True
    assert cards["staffing.assign-manager"].property("adminNotificationFilterMatch") is False

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationClearFilters").click()
    assert event_filter.currentText() == "All events"
    assert not cards["offer.generated"].isHidden()
    assert not cards["staffing.assign-manager"].isHidden()
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_template_filter_hides_by_subject_body_completeness(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            active=True,
            subject_template="Approved: {candidate_name}",
            body_template="{candidate_name} approved.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="custom.missing-subject",
            label="Missing subject",
            active=True,
            subject_template="",
            body_template="Body exists.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="custom.missing-body",
            label="Missing body",
            active=True,
            subject_template="Subject exists",
            body_template="",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    template_filter = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationTemplateFilter")
    assert template_filter is not None
    cards = {
        card.property("adminNotificationEvent"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    }

    template_filter.setCurrentText("Missing subject")
    assert cards["offer.approved"].isHidden()
    assert not cards["custom.missing-subject"].isHidden()
    assert cards["custom.missing-body"].isHidden()

    template_filter.setCurrentText("Missing body")
    assert cards["offer.approved"].isHidden()
    assert cards["custom.missing-subject"].isHidden()
    assert not cards["custom.missing-body"].isHidden()

    template_filter.setCurrentText("Complete templates")
    assert not cards["offer.approved"].isHidden()
    assert cards["custom.missing-subject"].isHidden()
    assert cards["custom.missing-body"].isHidden()

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationClearFilters").click()
    assert template_filter.currentText() == "All templates"
    assert not cards["offer.approved"].isHidden()
    assert not cards["custom.missing-subject"].isHidden()
    assert not cards["custom.missing-body"].isHidden()
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_renders_all_rule_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    for index in range(1, 9):
        store.save_rule(
            NotificationRule(
                event_type=f"custom.rule-{index}",
                label=f"Custom rule {index}",
                active=True,
                subject_template=f"Subject {index}",
                body_template=f"Body {index}",
                recipients=[NotificationRecipient(email=f"recipient{index}@example.org")],
            )
        )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    cards = window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard")
    last_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_custom_rule_8")

    assert len(cards) == len(window.admin_draft.notification_rules)
    assert last_button is not None
    last_button.click()
    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioNotificationRuleEvent").text() == "custom.rule-8"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_sort_reorders_rule_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    store.save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager",
            active=True,
            subject_template="Manager: {position_name}",
            body_template="Review {position_name}.",
            recipients=[
                NotificationRecipient(email="manager@example.org"),
                NotificationRecipient(email="director@example.org"),
            ],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="Offer accepted",
            active=False,
            subject_template="Accepted: {candidate_name}",
            body_template="{candidate_name} accepted.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="custom.reminder",
            label="Reminder",
            active=True,
            subject_template="Reminder",
            body_template="Reminder",
            recipients=[
                NotificationRecipient(email="one@example.org"),
                NotificationRecipient(email="two@example.org"),
                NotificationRecipient(email="three@example.org"),
            ],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationRuleListPanel")
    sort = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationSortBy")
    assert panel is not None
    assert sort is not None

    sort.setCurrentText("Sort by: Event")
    assert panel.layout().itemAt(2).widget().property("adminNotificationEvent") == "custom.reminder"

    sort.setCurrentText("Sort by: Recipients")

    assert panel.layout().itemAt(2).widget().property("adminNotificationEvent") == "custom.reminder"
    assert panel.layout().itemAt(2).widget().property("adminNotificationSortRank") == 0
    assert panel.layout().itemAt(3).widget().property("adminNotificationEvent") == "staffing.assign-manager"
    assert panel.layout().itemAt(4).widget().property("adminNotificationEvent") == "offer.accepted"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notifications_toolbar_scrolls_and_toggles_card_view(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assign-manager",
            label="Hiring manager",
            active=True,
            subject_template="Manager: {position_name}",
            body_template="Review {position_name}.",
            recipients=[NotificationRecipient(email="manager@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)

    panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioNotificationRuleListPanel")
    toolbar_scroll = window.window.findChild(qt_widgets.QScrollArea, "AdminStudioNotificationToolbarScroll")
    view_toggle = window.window.findChild(qt_widgets.QComboBox, "AdminStudioNotificationViewToggle")

    assert panel is not None
    assert toolbar_scroll is not None
    assert toolbar_scroll.horizontalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert view_toggle is not None
    assert panel.property("adminNotificationViewMode") == "list"

    view_toggle.setCurrentText("Grid")

    assert panel.property("adminNotificationViewMode") == "grid"
    for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioNotificationRuleCard"):
        assert card.property("adminNotificationViewMode") == "grid"
    window.window.close()
    app.processEvents()

def test_pyside_admin_notification_validation_warns_on_unknown_variables(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore

    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="custom.unknown-variable",
            label="Unknown variable check",
            active=True,
            subject_template="Position needed now: {unknown_subject}",
            body_template="Hi {hiring_manager_name}, review {unknown_body}.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(6)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioNotificationRuleButton_custom_unknown_variable").click()

    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioNotificationRuleValidation")
    assert validation is not None
    assert "Unknown variables: unknown_body, unknown_subject" in validation.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_email_settings_save_feed_notification_sending(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    from notification_models import NotificationRecipient, NotificationRule
    from notification_store import NotificationStore
    import notification_service
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    email_settings_path = tmp_path / "email_account_settings.json"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="Offer accepted",
            active=True,
            subject_template="Offer accepted: {candidate_name}",
            body_template="{candidate_name} accepted {position}.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    monkeypatch.setattr(pyside_interview_app, "EMAIL_ACCOUNT_SETTINGS_PATH", email_settings_path)
    monkeypatch.setattr(notification_service, "EMAIL_ACCOUNT_SETTINGS_PATH", email_settings_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    email_row = next(
        row
        for row in range(section_list.count())
        if section_list.item(row).text() == "Email Settings"
    )
    section_list.setCurrentRow(email_row)

    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailAccountLabel").setText("Gmail - Notifications")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailAddress").setText("CherylParsons2019@gmail.com")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailDisplayName").setText("Cheryl Parsons")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailIncomingUsername").setText("CherylParsons2019@gmail.com")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailIncomingPassword").setText("app-password")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailImapServer").setText("imap.gmail.com")
    window.window.findChild(qt_widgets.QSpinBox, "AdminStudioEmailImapPort").setValue(993)
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailSmtpServer").setText("smtp.gmail.com")
    window.window.findChild(qt_widgets.QSpinBox, "AdminStudioEmailSmtpPort").setValue(587)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailSaveSettingsButton").click()

    saved = notification_service.load_email_account_settings(email_settings_path)
    assert saved.sender_email == "CherylParsons2019@gmail.com"
    assert saved.smtp_host == "smtp.gmail.com"
    assert saved.smtp_port == 587
    assert saved.smtp_username == "CherylParsons2019@gmail.com"
    assert saved.smtp_password == "app-password"
    assert saved.imap_or_pop_host == "imap.gmail.com"
    assert saved.imap_or_pop_port == 993

    sent = []

    def fake_send(settings, recipients, subject, body):
        sent.append((settings, recipients, subject, body))

    monkeypatch.setattr(notification_service, "_send_email_message", fake_send)
    service = notification_service.notification_service_from_email_account_settings(
        settings_path=email_settings_path,
        store_path=notification_rules_path,
    )
    results = service.emit_event(
        "offer.accepted",
        {"candidate_name": "Maya Patel", "position": "Teacher"},
        "email-settings-offer",
    )

    assert results[0].status == "sent"
    assert sent[0][0].smtp_host == "smtp.gmail.com"
    assert sent[0][0].sender_email == "CherylParsons2019@gmail.com"
    assert sent[0][1] == ["director@example.org"]
    assert sent[0][2] == "Offer accepted: Maya Patel"
    window.window.close()
    app.processEvents()

def test_pyside_admin_email_test_connection_verifies_smtp_without_saving(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    email_settings_path = tmp_path / "email_account_settings.json"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    monkeypatch.setattr(pyside_interview_app, "EMAIL_ACCOUNT_SETTINGS_PATH", email_settings_path)
    verified = []

    def fake_verify(settings):
        verified.append(settings)

    monkeypatch.setattr(pyside_interview_app, "verify_email_connection", fake_verify)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    email_row = next(
        row
        for row in range(section_list.count())
        if section_list.item(row).text() == "Email Settings"
    )
    section_list.setCurrentRow(email_row)

    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailAddress").setText("HR@example.org")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailIncomingUsername").setText("HR@example.org")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailIncomingPassword").setText("app-password")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailSmtpServer").setText("smtp.example.org")
    window.window.findChild(qt_widgets.QSpinBox, "AdminStudioEmailSmtpPort").setValue(587)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailTestConnectionButton").click()

    assert not email_settings_path.exists()
    assert verified[0].sender_email == "HR@example.org"
    assert verified[0].smtp_host == "smtp.example.org"
    status = window.window.findChild(qt_widgets.QLabel, "AdminStudioEmailConnectionStatus")
    assert "Connection verified" in status.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_email_account_type_is_exclusive_and_saved(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    import notification_service
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    email_settings_path = tmp_path / "email_account_settings.json"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    monkeypatch.setattr(pyside_interview_app, "EMAIL_ACCOUNT_SETTINGS_PATH", email_settings_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    email_row = next(row for row in range(section_list.count()) if section_list.item(row).text() == "Email Settings")
    section_list.setCurrentRow(email_row)

    imap = window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailImapAccountType")
    pop3 = window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailPop3AccountType")
    assert imap.isChecked() is True
    assert pop3.isChecked() is False

    pop3.click()
    assert pop3.isChecked() is True
    assert imap.isChecked() is False
    imap.click()
    assert imap.isChecked() is True
    assert pop3.isChecked() is False
    pop3.click()
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailAddress").setText("HR@example.org")
    window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailSmtpServer").setText("smtp.example.org")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailSaveSettingsButton").click()

    assert notification_service.load_email_account_settings(email_settings_path).account_type == "POP3"
    window.window.close()
    app.processEvents()

def test_pyside_admin_email_account_type_updates_provider_defaults_and_mockup_panels(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    email_settings_path = tmp_path / "email_account_settings.json"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    monkeypatch.setattr(pyside_interview_app, "EMAIL_ACCOUNT_SETTINGS_PATH", email_settings_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    email_row = next(row for row in range(section_list.count()) if section_list.item(row).text() == "Email Settings")
    section_list.setCurrentRow(email_row)

    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioEmailIdentityPanel") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioEmailIncomingPanel") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioEmailOutgoingPanel") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioEmailActionsBar") is not None
    incoming_title = window.window.findChild(qt_widgets.QLabel, "AdminStudioEmailIncomingTitle")
    incoming_server = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioEmailImapServer")
    incoming_port = window.window.findChild(qt_widgets.QSpinBox, "AdminStudioEmailImapPort")
    encryption = window.window.findChild(qt_widgets.QComboBox, "AdminStudioEmailIncomingEncryption")
    pop3 = window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailPop3AccountType")
    imap = window.window.findChild(qt_widgets.QPushButton, "AdminStudioEmailImapAccountType")

    pop3.click()

    assert incoming_title.text() == "Incoming mail (POP3)"
    assert incoming_server.text() == "pop.gmail.com"
    assert incoming_port.value() == 995
    assert encryption.currentText() == "SSL/TLS"

    imap.click()

    assert incoming_title.text() == "Incoming mail (IMAP)"
    assert incoming_server.text() == "imap.gmail.com"
    assert incoming_port.value() == 993
    assert encryption.currentText() == "SSL/TLS"
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_school_drawer_saves_folder_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Hawthorne": {
                    "interview_notes_dir": "C:/safe/hawthorne",
                    "full_time_template": "C:/templates/standard.docx",
                    "part_time_template": "C:/templates/part-time.docx",
                },
                "Palmdale": {"interview_notes_dir": ""},
            }
        ),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    hawthorne_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderButton_Hawthorne")
    drawer_title = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolDetailTitle")
    full_path = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioSchoolFolderPath")
    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolValidationNotes")
    linked_templates = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLinkedTemplates")
    save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderSave")

    assert hawthorne_button is not None
    hawthorne_button.click()
    assert drawer_title.text() == "Hawthorne"
    assert full_path.text() == "C:/safe/hawthorne"
    assert "Path exists and is accessible" in validation.text()
    assert "Standard Offer" in linked_templates.text()
    assert "standard.docx" in linked_templates.text()
    assert full_path.isEnabled() is False
    assert save.isEnabled() is False

    window.admin_edit_button.click()
    assert full_path.isEnabled() is True
    full_path.setText("C:/safe/hawthorne-updated")
    save.click()
    window.admin_save_draft_button.click()

    assert window.admin_draft.school_settings["Hawthorne"]["interview_notes_dir"] == "C:/safe/hawthorne-updated"
    settings_table = window.window.findChild(qt_widgets.QTableWidget, "PySideSchoolFolderSettingsTable")
    hawthorne_row = next(
        row
        for row in range(settings_table.rowCount())
        if settings_table.item(row, 0) and settings_table.item(row, 0).text() == "Hawthorne"
    )
    assert settings_table.item(hawthorne_row, 1).text() == "C:/safe/hawthorne-updated"
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_can_add_and_delete_school_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps({"Hawthorne": {"interview_notes_dir": "C:/safe/hawthorne"}}),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    add_school = window.window.findChild(qt_widgets.QPushButton, "AdminStudioAddSchoolButton")
    delete_school = window.window.findChild(qt_widgets.QPushButton, "AdminStudioDeleteSchoolButton")
    school_name = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioSchoolName")
    folder_path = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioSchoolFolderPath")
    save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderSave")

    assert add_school is not None
    assert delete_school is not None
    assert school_name is not None
    assert add_school.isEnabled() is False
    assert delete_school.isEnabled() is False
    assert school_name.isEnabled() is False

    window.admin_edit_button.click()
    add_school.click()
    school_name.setText("New Campus")
    folder_path.setText("C:/safe/new-campus")
    save.click()

    assert window.admin_draft.school_settings["New Campus"]["interview_notes_dir"] == "C:/safe/new-campus"
    settings_table = window.window.findChild(qt_widgets.QTableWidget, "PySideSchoolFolderSettingsTable")
    table_schools = {
        settings_table.item(row, 0).text()
        for row in range(settings_table.rowCount())
        if settings_table.item(row, 0) is not None
    }
    assert "New Campus" in table_schools

    delete_school.click()

    assert "New Campus" not in window.admin_draft.school_settings
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolDetailTitle").text() == "Select a school"
    table_schools = {
        settings_table.item(row, 0).text()
        for row in range(settings_table.rowCount())
        if settings_table.item(row, 0) is not None
    }
    assert "New Campus" not in table_schools
    assert "Hawthorne" in window.admin_draft.school_settings
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_offer_template_health_panel_lists_active_templates(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    standard_template = tmp_path / "templates" / "standard.docx"
    director_template = tmp_path / "templates" / "director.docx"
    contractor_template = tmp_path / "templates" / "contractor.docx"
    standard_template.parent.mkdir()
    standard_template.write_text("standard", encoding="utf-8")
    director_template.write_text("director", encoding="utf-8")
    contractor_template.write_text("contractor", encoding="utf-8")
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Hawthorne": {
                    "interview_notes_dir": "C:/safe/hawthorne",
                    "full_time_template": str(standard_template),
                    "part_time_template": str(director_template),
                    "contractor_template": str(contractor_template),
                }
            }
        ),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    panel = window.window.findChild(qt_widgets.QFrame, "AdminStudioOfferTemplateHealthPanel")
    assert panel is not None
    text = _widget_text(panel)
    assert "Offer Template Health" in text
    assert "3 active templates" in text
    assert "Standard Offer" in text
    assert "Director Offer" in text
    assert "Contractor Offer" in text
    assert len(panel.findChildren(qt_widgets.QFrame, "AdminStudioOfferTemplateHealthCard")) == 3
    assert panel.findChild(qt_widgets.QPushButton, "AdminStudioNewTemplateButton") is not None
    view_all = panel.findChild(qt_widgets.QPushButton, "AdminStudioViewAllTemplatesButton")
    assert view_all is not None
    view_all.click()
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolDetailTitle").text() == "Hawthorne"
    assert "Standard Offer" in window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLinkedTemplates").text()
    assert "Viewing all configured offer templates" in window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLastTestWrite").text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_new_template_adds_selected_school_template(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    template_path = tmp_path / "standard-offer.docx"
    template_path.write_text("offer template", encoding="utf-8")
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps({"Hawthorne": {"interview_notes_dir": "C:/safe/hawthorne"}}),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    monkeypatch.setattr(
        qt_widgets.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(template_path), "Word documents (*.docx)"),
    )
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    new_template = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNewTemplateButton")
    linked_templates = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLinkedTemplates")
    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolValidationNotes")

    assert new_template is not None
    assert new_template.isEnabled() is False

    window.admin_edit_button.click()
    new_template.click()

    assert window.admin_draft.school_settings["Hawthorne"]["full_time_template"] == str(template_path)
    assert "Standard Offer" in linked_templates.text()
    assert "standard-offer.docx" in linked_templates.text()
    assert "Template added to draft" in validation.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_test_write_checks_selected_folder(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    target_dir = tmp_path / "school-folders" / "palmdale"
    target_dir.mkdir(parents=True)
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps({"Palmdale": {"interview_notes_dir": str(target_dir)}}),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderButton_Palmdale").click()
    test_write = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolTestWriteButton")
    assert test_write is not None
    test_write.click()

    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolValidationNotes")
    last_test = window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLastTestWrite")
    assert "Write permission confirmed" in validation.text()
    assert "Result: Passed" in last_test.text()
    assert not (target_dir / "_admin_studio_test_write.tmp").exists()
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_copy_path_copies_selected_folder(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    target_dir = tmp_path / "school-folders" / "palmdale"
    target_dir.mkdir(parents=True)
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps({"Palmdale": {"interview_notes_dir": str(target_dir)}}),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderButton_Palmdale").click()
    copy_path = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolCopyPathButton")
    assert copy_path is not None
    app.clipboard().clear()
    copy_path.click()

    assert app.clipboard().text() == str(target_dir)
    assert "Copied" in window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLastTestWrite").text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_school_card_actions_use_selected_school(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    hawthorne_dir = tmp_path / "school-folders" / "hawthorne"
    palmdale_dir = tmp_path / "school-folders" / "palmdale"
    hawthorne_dir.mkdir(parents=True)
    palmdale_dir.mkdir(parents=True)
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Hawthorne": {"interview_notes_dir": str(hawthorne_dir)},
                "Palmdale": {"interview_notes_dir": str(palmdale_dir)},
            }
        ),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Hawthorne", "Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    app.clipboard().clear()
    copy_path = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolCardCopyPathButton_Palmdale")
    test_write = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolCardTestWriteButton_Palmdale")

    assert copy_path is not None
    assert test_write is not None
    copy_path.click()

    assert app.clipboard().text() == str(palmdale_dir)
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolDetailTitle").text() == "Palmdale"
    assert "Copied" in window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolLastTestWrite").text()

    test_write.click()

    assert "Write permission confirmed" in window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolValidationNotes").text()
    assert not (palmdale_dir / "_admin_studio_test_write.tmp").exists()
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_school_cards_track_selected_state(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "Hawthorne": {"interview_notes_dir": "C:/safe/hawthorne"},
                "Palmdale": {"interview_notes_dir": "C:/safe/palmdale"},
            }
        ),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Hawthorne", "Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)

    hawthorne = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderButton_Hawthorne")
    palmdale = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderButton_Palmdale")

    assert hawthorne is not None
    assert palmdale is not None

    palmdale.click()

    assert hawthorne.property("adminSchoolSelected") is False
    assert palmdale.property("adminSchoolSelected") is True
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolSelectedBadge_Palmdale").property("adminSchoolSelected") is True
    assert window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolDetailTitle").text() == "Palmdale"
    window.window.close()
    app.processEvents()

def test_pyside_admin_templates_browse_folder_updates_selected_path(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    initial_dir = tmp_path / "school-folders" / "palmdale"
    selected_dir = tmp_path / "school-folders" / "palmdale-updated"
    initial_dir.mkdir(parents=True)
    selected_dir.mkdir(parents=True)
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(
        json.dumps({"Palmdale": {"interview_notes_dir": str(initial_dir)}}),
        encoding="utf-8",
    )
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(5)
    monkeypatch.setattr(
        window.QtWidgets.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(selected_dir),
    )

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolFolderButton_Palmdale").click()
    window.admin_edit_button.click()
    browse = window.window.findChild(qt_widgets.QPushButton, "AdminStudioSchoolBrowseFolderButton")
    assert browse is not None
    browse.click()

    assert window.window.findChild(qt_widgets.QLineEdit, "AdminStudioSchoolFolderPath").text() == str(selected_dir)
    assert "Selected folder" in window.window.findChild(qt_widgets.QLabel, "AdminStudioSchoolValidationNotes").text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_editor_saves_prompt_draft(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(
        json.dumps(
            {
                "answer_summary_user": "Strict JSON summary from {payload_json}.",
                "executive_summary_user": "Executive summary from {transcript}.",
            }
        ),
        encoding="utf-8",
    )
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)

    prompt_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user")
    title = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptEditorTitle")
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptValidation")
    variables = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptVariables")
    candidate_variable = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptVariable_candidate_name")
    save = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave")

    assert prompt_button is not None
    prompt_button.click()
    assert title.text() == "answer_summary_user"
    assert editor.toPlainText() == "Strict JSON summary from {payload_json}."
    assert "{payload_json}" in variables.text()
    assert candidate_variable is not None
    assert validation.text() == "JSON/text prompt looks ready."
    assert editor.isEnabled() is False
    assert save.isEnabled() is False

    window.admin_edit_button.click()
    assert editor.isEnabled() is True
    editor.setPlainText("Updated prompt using {payload_json} and .")
    cursor = editor.textCursor()
    cursor.setPosition(editor.toPlainText().index(" and ") + len(" and "))
    editor.setTextCursor(cursor)
    candidate_variable.click()
    assert editor.toPlainText() == "Updated prompt using {payload_json} and {candidate_name}."
    save.click()
    window.admin_save_draft_button.click()

    assert window.admin_draft.prompts["answer_summary_user"] == "Updated prompt using {payload_json} and {candidate_name}."
    prompts_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioPromptsTable")
    prompt_row = next(
        row
        for row in range(prompts_table.rowCount())
        if prompts_table.item(row, 0) and prompts_table.item(row, 0).text() == "answer_summary_user"
    )
    assert prompts_table.item(prompt_row, 1).text() == "Updated prompt using {payload_json} and {candidate_name}."
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_version_note_unblocks_prompt_publish_validation(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    window.admin_edit_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    note = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioPromptVersionNote")

    assert note is not None
    assert note.isEnabled() is True
    editor.setPlainText("Updated {payload_json}.")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave").click()
    assert "requires version notes" in "\n".join(window.admin_draft.validate())

    note.setText("Clarify prompt output for summary reruns.")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave").click()

    assert not [error for error in window.admin_draft.validate() if "requires version notes" in error]
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_summary_strip_matches_mockup(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)

    strip = window.window.findChild(qt_widgets.QFrame, "AdminStudioPromptSummaryStrip")
    cards = window.window.findChildren(qt_widgets.QFrame, "AdminStudioPromptSummaryCard")

    assert strip is not None
    assert len(cards) == 4
    strip_text = _widget_text(strip)
    assert "Variables" in strip_text
    assert "12 tokens" in strip_text
    assert "Preview" in strip_text
    assert "Live preview" in strip_text
    assert "Version review" in strip_text
    assert "5 versions" in strip_text
    assert "Validation" in strip_text
    assert "warning" in strip_text.lower()
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSettingsButton").text() == "Prompt Settings"
    assert window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSummaryNewPromptButton").text() == "New Prompt"
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_settings_opens_prompt_settings_dialog(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSettingsButton").click()
    app.processEvents()

    dialog = next(
        widget
        for widget in app.topLevelWidgets()
        if widget.objectName() == "AdminStudioPromptSettingsDialog" and widget.isVisible()
    )
    assert "Prompt Settings" in dialog.windowTitle()
    text = _widget_text(dialog)
    assert "Required variables" in text
    assert "answer_summary_user requires {payload_json}" in text
    assert "Unknown variables block publishing" in text
    assert section_list.currentItem().text() == "DeepSeek Prompts"
    dialog.close()
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_selected_metadata_matches_mockup(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(
        json.dumps({"answer_summary_user": "Summarize {payload_json}.", "executive_summary_user": "Summarize {transcript}."}),
        encoding="utf-8",
    )
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    status = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptSelectedStatus")
    description = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptSelectedDescription")
    metadata = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptSelectedMetadata")

    assert status is not None
    assert status.text() == "Warning"
    assert description.text() == "Early childhood hiring notes summary"
    assert "Version: v3" in metadata.text()
    assert "Status: Draft" in metadata.text()
    assert "Last modified: May 21, 2025 · 10:42 AM" in metadata.text()

    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_executive_summary_user").click()
    assert status.text() == "OK"
    assert description.text() == "Executive summary section"
    assert "Version: v2" in metadata.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_search_filters_template_cards(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(
        json.dumps(
            {
                "answer_summary_user": "Early childhood hiring notes from {payload_json}.",
                "executive_summary_user": "Executive summary section from {transcript}.",
            }
        ),
        encoding="utf-8",
    )
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)

    search = window.window.findChild(qt_widgets.QLineEdit, "AdminStudioPromptSearch")
    cards = {
        card.property("adminPromptKey"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioPromptTemplateCard")
    }

    assert search is not None
    assert search.placeholderText() == "Search prompts..."
    assert {"answer_summary_user", "executive_summary_user"}.issubset(set(cards))
    search.setText("executive")
    app.processEvents()
    assert cards["answer_summary_user"].isHidden() is True
    assert cards["executive_summary_user"].isHidden() is False

    search.setText("early childhood")
    app.processEvents()
    assert cards["answer_summary_user"].isHidden() is False
    assert cards["executive_summary_user"].isHidden() is True

    search.clear()
    app.processEvents()
    assert all(card.isHidden() is False for card in cards.values())
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_new_template_creates_draft_card(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)

    new_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioNewPromptTemplateButton")
    title = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptEditorTitle")
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")

    assert new_button is not None
    assert new_button.text() == "New Prompt Template"
    assert new_button.isEnabled() is False

    window.admin_edit_button.click()
    new_button.click()
    app.processEvents()

    assert title.text() == "custom_prompt_1"
    assert editor.toPlainText() == ""
    assert window.admin_draft.prompts["custom_prompt_1"] == ""
    cards = {
        card.property("adminPromptKey"): card
        for card in window.window.findChildren(qt_widgets.QFrame, "AdminStudioPromptTemplateCard")
    }
    assert "custom_prompt_1" in cards
    prompts_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioPromptsTable")
    table_keys = {prompts_table.item(row, 0).text() for row in range(prompts_table.rowCount())}
    assert "custom_prompt_1" in table_keys
    assert "Unsaved changes:" in window.admin_unsaved_pill.text()
    assert window.admin_unsaved_pill.text() != "Unsaved changes: 0"
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_variable_chips_include_all_visible_tokens(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.admin_edit_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    editor.setPlainText("")

    expected = [
        "payload_json",
        "transcript",
        "track",
        "candidate_name",
        "flow_index",
        "question_id",
        "question_label",
        "skipped",
        "timestamp",
    ]
    for variable in expected:
        button = window.window.findChild(qt_widgets.QPushButton, f"AdminStudioPromptVariable_{variable}")
        assert button is not None, variable
        assert button.text() == f"{{{variable}}}"
        assert button.isEnabled() is True
        button.click()

    text = editor.toPlainText()
    for variable in expected:
        assert f"{{{variable}}}" in text
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_validation_warns_on_unknown_variables(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {missing_prompt_var}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptValidation")
    assert "Unknown variables: missing_prompt_var" in validation.text()

    window.admin_edit_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    editor.setPlainText("Summarize {payload_json}.")
    app.processEvents()
    assert validation.text() == "JSON/text prompt looks ready."
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_validation_warns_on_missing_required_variables(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize available answer evidence."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    validation = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptValidation")
    assert "Required variables missing: payload_json" in validation.text()

    window.admin_edit_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    cursor = editor.textCursor()
    cursor.movePosition(qt_gui.QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptVariable_payload_json").click()
    app.processEvents()

    assert validation.text() == "JSON/text prompt looks ready."
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_editor_footer_tracks_cursor_and_format(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    footer = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptEditorFooter")

    assert footer is not None
    assert "JSON" in footer.text()
    assert "Ln 1, Col 1" in footer.text()
    assert "Spaces: 2" in footer.text()
    assert "UTF-8" in footer.text()
    assert "LF" in footer.text()

    window.admin_edit_button.click()
    editor.setPlainText("First line\nSecond line")
    cursor = editor.textCursor()
    cursor.movePosition(qt_gui.QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    app.processEvents()

    assert "Ln 2" in footer.text()
    assert "Col 12" in footer.text()
    assert "JSON" in footer.text()
    assert "Spaces: 2" in footer.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_editor_format_dropdown_and_expand_dialog(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    format_dropdown = window.window.findChild(qt_widgets.QComboBox, "AdminStudioPromptFormatDropdown")
    footer = window.window.findChild(qt_widgets.QLabel, "AdminStudioPromptEditorFooter")
    expand = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptExpandButton")

    assert format_dropdown is not None
    assert [format_dropdown.itemText(index) for index in range(format_dropdown.count())] == ["JSON", "Text"]
    assert format_dropdown.currentText() == "JSON"
    assert "JSON |" in footer.text()
    format_dropdown.setCurrentText("Text")
    app.processEvents()
    assert "Text |" in footer.text()

    assert expand is not None
    expand.click()
    dialog = window.admin_prompt_expand_dialog
    expanded_editor = dialog.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptExpandEditor")

    assert dialog.objectName() == "AdminStudioPromptExpandDialog"
    assert "Expanded Prompt Editor" in dialog.windowTitle()
    assert expanded_editor is not None
    assert expanded_editor.toPlainText() == "Summarize {payload_json}."
    assert expanded_editor.isEnabled() == window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor").isEnabled()
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_inspector_tabs_show_variables_and_activity(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    tabs = window.window.findChild(qt_widgets.QTabWidget, "AdminStudioPromptInspectorTabs")

    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == ["Inspector", "Activity"]
    assert "payload_json" in _widget_text(tabs.widget(0))
    assert "Open Preview" in _widget_text(tabs.widget(0))
    assert "Required variables" in _widget_text(tabs.widget(0))
    assert "v3" in _widget_text(tabs.widget(1))
    assert "David Nord" in _widget_text(tabs.widget(1))
    assert "Save Draft updates version history" in _widget_text(tabs.widget(1))
    window.window.close()
    app.processEvents()

def test_pyside_admin_prompt_preview_modal_renders_sample_prompt(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(
        json.dumps({"answer_summary_user": "Summarize {transcript} for {candidate_name}. Return {payload_json}. {missing_var}"}),
        encoding="utf-8",
    )
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    preview_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptPreviewButton")
    assert preview_button is not None
    preview_button.click()
    dialog = window.admin_prompt_preview_dialog

    assert dialog.objectName() == "AdminStudioPromptPreviewDialog"
    assert "Prompt Preview" in dialog.windowTitle()
    text = _widget_text(dialog)
    assert "Maya Patel" in text
    assert "Candidate described calming a child during transition." in text
    assert "JSON validation: ready" in text
    assert "Model response preview" in text
    assert "Unresolved variables: missing_var" in text
    window.window.close()
    app.processEvents()

def test_pyside_admin_version_history_dialog_shows_prompt_versions(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user").click()

    history = window.window.findChild(qt_widgets.QPushButton, "AdminStudioVersionHistoryButton")
    assert history is not None
    history.click()
    dialog = window.admin_version_history_dialog

    assert dialog.objectName() == "AdminStudioVersionHistoryDialog"
    assert "Version History" in dialog.windowTitle()
    text = _widget_text(dialog)
    assert "answer_summary_user" in text
    assert "v3" in text
    assert "Draft" in text
    assert "Published" in text
    assert "David Nord" in text
    assert "Changed prompt template" in text
    window.window.close()
    app.processEvents()

def test_pyside_admin_global_version_history_covers_admin_artifact_types(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")

    button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioGlobalVersionHistoryButton")

    assert button is not None
    button.click()
    dialog = window.admin_version_history_dialog

    assert dialog.objectName() == "AdminStudioVersionHistoryDialog"
    text = _widget_text(dialog)
    assert "Who changed what and when" in text
    assert "Prompts" in text
    assert "Rubrics" in text
    assert "Notifications" in text
    assert "JSON files" in text
    assert "David Nord" in text
    assert len(dialog.findChildren(qt_widgets.QFrame, "AdminStudioVersionHistoryEntry")) >= 4
    window.window.close()
    app.processEvents()

def test_pyside_admin_discard_confirmation_modal_lists_lost_changes(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(9)
    window.admin_edit_button.click()
    prompt_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptTemplateButton_answer_summary_user")
    prompt_button.click()
    editor = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioPromptTemplateEditor")
    editor.setPlainText("Discard me.")
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioPromptSave").click()

    dialog = window._build_admin_discard_confirmation_dialog(window.admin_draft.change_summary())

    assert dialog.objectName() == "AdminStudioDiscardConfirmationDialog"
    assert "Discard Changes" in dialog.windowTitle()
    assert "discarded" in _widget_text(dialog)
    section_card = dialog.findChild(qt_widgets.QFrame, "AdminStudioDiscardSectionSummaryCard")
    assert section_card is not None
    assert "DeepSeek Prompts" in _widget_text(section_card)
    assert "deepseek_prompts.json" in _widget_text(dialog)
    assert "Discard me." in _widget_text(dialog)
    confirm = dialog.findChild(qt_widgets.QPushButton, "AdminStudioConfirmDiscardButton")
    assert confirm is not None
    confirm.click()

    assert window.admin_draft.prompts["answer_summary_user"] == "Summarize."
    assert window.admin_edit_mode is False
    window.window.close()
    app.processEvents()

def test_pyside_admin_add_track_modal_creates_draft_track(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")

    add_track = window.window.findChild(qt_widgets.QPushButton, "AdminStudioAddTrackButton")
    assert add_track is not None
    assert add_track.isEnabled() is False
    window.admin_edit_button.click()
    assert add_track.isEnabled() is True

    add_track.click()
    dialog = window.admin_track_dialog
    assert dialog.objectName() == "AdminStudioTrackDialog"
    assert "Create/Edit Track" in dialog.windowTitle()
    dialog.findChild(qt_widgets.QLineEdit, "AdminStudioTrackName").setText("Infant/Toddler")
    dialog.findChild(qt_widgets.QLineEdit, "AdminStudioTrackKey").setText("infant_toddler")
    dialog.findChild(qt_widgets.QPlainTextEdit, "AdminStudioTrackDescription").setPlainText("Infant and toddler interview flow.")
    dialog.findChild(qt_widgets.QCheckBox, "AdminStudioTrackActive").setChecked(True)
    dialog.findChild(qt_widgets.QPushButton, "AdminStudioSaveTrackButton").click()

    assert window.admin_draft.rubric["tracks"]["infant_toddler"]["label"] == "Infant/Toddler"
    assert window.admin_draft.overrides["track_question_flow"]["infant_toddler"] == []
    assert window.admin_tracks_pill.text() == "Tracks: 2"
    assert "Unsaved changes:" in window.admin_unsaved_pill.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_selects_file_and_shows_readonly_viewer(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({"Palmdale": {"interview_notes_dir": "C:/safe/palmdale"}}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)

    prompts_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioJsonFileButton_deepseek_prompts_json")
    selected_file = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonSelectedFile")
    viewer = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonCodeViewer")
    line_numbers = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonLineNumbers")
    detail_path = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonFilePath")
    detail_status = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonValidationResult")
    summary = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonFileSummary")

    assert prompts_button is not None
    assert selected_file.text() == "rubric.json"
    assert viewer.isReadOnly() is True
    highlighter = viewer.document().findChild(qt_core.QObject, "AdminStudioJsonSyntaxHighlighter")
    assert highlighter is not None
    assert highlighter.property("adminSyntax") == "json"
    assert line_numbers is not None
    assert line_numbers.isReadOnly() is True
    assert line_numbers.toPlainText().splitlines()[:3] == ["1", "2", "3"]
    assert '"metadata"' in viewer.toPlainText()

    prompts_button.click()

    assert selected_file.text() == "deepseek_prompts.json"
    assert "answer_summary_user" in viewer.toPlainText()
    assert line_numbers.toPlainText().splitlines()[-1] == str(viewer.document().blockCount())
    assert str(prompts_path) in detail_path.text()
    assert detail_status.text() in {"Healthy", "1 issue"}
    assert "AI prompt templates" in summary.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_copy_button_copies_visible_viewer_text(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioJsonFileButton_deepseek_prompts_json").click()

    viewer = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonCodeViewer")
    copy = window.window.findChild(qt_widgets.QPushButton, "AdminStudioCopyJsonViewerButton")
    status = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonCopyStatus")

    assert copy is not None
    assert status is not None
    app.clipboard().clear()
    copy.click()

    assert app.clipboard().text() == viewer.toPlainText()
    assert "Copied deepseek_prompts.json" in status.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_expand_opens_read_only_viewer(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioJsonFileButton_deepseek_prompts_json").click()

    viewer = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonCodeViewer")
    expand = window.window.findChild(qt_widgets.QPushButton, "AdminStudioExpandJsonViewerButton")

    assert expand is not None
    expand.click()
    dialog = window.admin_json_expand_dialog
    expanded = dialog.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonExpandViewer")

    assert dialog.objectName() == "AdminStudioJsonExpandDialog"
    assert "Expanded JSON Viewer" in dialog.windowTitle()
    assert "deepseek_prompts.json" in _widget_text(dialog)
    assert expanded is not None
    assert expanded.isReadOnly() is True
    assert expanded.toPlainText() == viewer.toPlainText()
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_marks_selected_file_and_copies_detail_path(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)

    rubric_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonFileCard_rubric_json")
    prompts_card = window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonFileCard_deepseek_prompts_json")
    prompts_button = window.window.findChild(qt_widgets.QPushButton, "AdminStudioJsonFileButton_deepseek_prompts_json")
    copy_path = window.window.findChild(qt_widgets.QPushButton, "AdminStudioCopyJsonPathButton")
    copy_status = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonCopyStatus")

    assert rubric_card is not None
    assert prompts_card is not None
    assert copy_path is not None
    assert rubric_card.property("adminJsonSelected") is True
    assert prompts_card.property("adminJsonSelected") is False

    prompts_button.click()

    assert rubric_card.property("adminJsonSelected") is False
    assert prompts_card.property("adminJsonSelected") is True
    app.clipboard().clear()
    copy_path.click()

    assert app.clipboard().text() == str(prompts_path)
    assert "Copied path for deepseek_prompts.json" in copy_status.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_mockup_summary_and_issue_panels(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    invalid_rubric_path = tmp_path / "invalid-admin-rubric.json"
    invalid_rubric_path.write_text('{\n  "metadata": {\n    "version": "test",\n', encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", invalid_rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)

    summary = window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonSummaryStrip")
    issue = window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonIssueCard")
    readonly = window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonReadOnlyNoticePanel")
    footer = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonViewerFooter")
    viewer = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonCodeViewer")
    line_numbers = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonLineNumbers")

    assert summary is not None
    assert len(summary.findChildren(qt_widgets.QFrame, "AdminStudioJsonSummaryCard")) == 4
    assert "Read-only review" in _widget_text(summary)
    assert "Open in editor" in _widget_text(summary)
    assert issue is not None
    assert "Line" in _widget_text(issue)
    assert "Expecting" in _widget_text(issue)
    assert readonly is not None
    assert "read-only view" in _widget_text(readonly)
    assert footer is not None
    assert "Line" in footer.text()
    assert "1 issue" in footer.text()
    gutter_lines = line_numbers.toPlainText().splitlines()
    assert gutter_lines[window.admin_json_issue_line - 1].startswith("!")
    issue_highlights = [
        selection
        for selection in viewer.extraSelections()
        if selection.cursor.blockNumber() == window.admin_json_issue_line - 1
        and selection.format.background().style() != qt_core.Qt.BrushStyle.NoBrush
    ]
    assert issue_highlights
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_issue_card_jumps_to_problem_line(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    invalid_rubric_path = tmp_path / "invalid-admin-rubric.json"
    invalid_rubric_path.write_text('{\n  "metadata": {\n    "version": "test",\n', encoding="utf-8")
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", invalid_rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)

    viewer = window.window.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonCodeViewer")
    jump = window.window.findChild(qt_widgets.QPushButton, "AdminStudioJsonIssueJumpButton")
    footer = window.window.findChild(qt_widgets.QLabel, "AdminStudioJsonViewerFooter")

    assert jump is not None
    assert jump.isEnabled() is True
    assert viewer.textCursor().blockNumber() == 0
    jump.click()

    assert viewer.textCursor().blockNumber() == 3
    assert "Line 4" in footer.text()
    window.window.close()
    app.processEvents()

def test_pyside_admin_json_editor_validates_and_saves_draft_payload(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({"Palmdale": {"interview_notes_dir": "C:/safe/palmdale"}}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)
    window.window.findChild(qt_widgets.QPushButton, "AdminStudioJsonFileButton_deepseek_prompts_json").click()

    open_editor = window.window.findChild(qt_widgets.QPushButton, "AdminStudioOpenJsonEditorButton")
    assert open_editor is not None
    open_editor.click()
    dialog = window.admin_json_editor_dialog

    assert dialog.objectName() == "AdminStudioJsonEditorDialog"
    assert "JSON Editor" in dialog.windowTitle()
    assert "Stronger warnings" in _widget_text(dialog)
    editor = dialog.findChild(qt_widgets.QPlainTextEdit, "AdminStudioJsonEditorText")
    status = dialog.findChild(qt_widgets.QLabel, "AdminStudioJsonEditorValidation")
    save = dialog.findChild(qt_widgets.QPushButton, "AdminStudioJsonEditorSaveDraft")
    assert editor is not None
    assert "answer_summary_user" in editor.toPlainText()
    assert editor.isEnabled() is False
    assert save.isEnabled() is False

    window.admin_edit_button.click()
    assert editor.isEnabled() is True
    assert save.isEnabled() is True

    editor.setPlainText('{\n  "answer_summary_user": "ok",\n  bad json\n}')
    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    save.click()
    assert "JSON validation error" in status.text()
    assert editor.textCursor().blockNumber() == 2
    assert window.admin_draft.prompts["answer_summary_user"] == "Summarize {payload_json}."

    editor.setPlainText(json.dumps({"answer_summary_user": "Updated from guarded JSON editor."}, indent=2))
    save.click()

    assert status.text() == "JSON validation: ready"
    assert window.admin_draft.prompts["answer_summary_user"] == "Updated from guarded JSON editor."
    assert json.loads(prompts_path.read_text(encoding="utf-8"))["answer_summary_user"] == "Summarize {payload_json}."
    window.window.close()
    app.processEvents()

def test_pyside_admin_advanced_json_uses_cards_without_legacy_table(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize {payload_json}."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:8b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    section_list = window.window.findChild(qt_widgets.QListWidget, "AdminStudioSectionList")
    section_list.setCurrentRow(11)

    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonFilesPanel") is not None
    assert window.window.findChild(qt_widgets.QFrame, "AdminStudioJsonFileDetailPanel") is not None
    assert window.window.findChild(qt_widgets.QTableWidget, "AdminStudioAdvancedTable") is None
    window.window.close()
    app.processEvents()

def test_pyside_admin_discard_requires_confirmation_and_reverts_table_edits(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    rubric_path = _write_test_rubric(tmp_path)
    overrides_path = _write_test_overrides(tmp_path)
    settings_path = tmp_path / "school_offer_settings.json"
    settings_path.write_text(json.dumps({}), encoding="utf-8")
    prompts_path = tmp_path / "deepseek_prompts.json"
    prompts_path.write_text(json.dumps({"answer_summary_user": "Summarize."}), encoding="utf-8")
    app_settings_path = tmp_path / "interview_app_settings.json"
    app_settings_path.write_text(json.dumps({"deepseek_summary_model": "deepseek-r1:14b"}), encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "DEFAULT_RUBRIC_PATH", rubric_path)
    monkeypatch.setattr(pyside_interview_app, "QUESTIONS_OVERRIDE_PATH", overrides_path)
    monkeypatch.setattr(pyside_interview_app, "SCHOOL_OFFER_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(pyside_interview_app, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompts_path)
    monkeypatch.setattr(pyside_interview_app, "INTERVIEW_APP_SETTINGS_PATH", app_settings_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Admin")
    questions_table = window.window.findChild(qt_widgets.QTableWidget, "AdminStudioQuestionsTable")
    window.admin_edit_button.click()
    questions_table.item(0, 4).setText("Changed question?")
    assert "Unsaved" in questions_table.item(0, 4).toolTip()

    answers = iter([
        window.QtWidgets.QMessageBox.StandardButton.No,
        window.QtWidgets.QMessageBox.StandardButton.Yes,
    ])
    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: next(answers))

    window._discard_admin_changes()
    assert questions_table.item(0, 4).text() == "Changed question?"
    assert window.admin_edit_mode is True

    window._discard_admin_changes()
    assert questions_table.item(0, 4).text() == "Why Launch Pad Learning?"
    assert window.admin_edit_mode is False
    window.window.close()
    app.processEvents()

def test_pyside_window_uses_native_title_minimize_and_maximize_controls() -> None:
    class FakeWindowType:
        Window = 1
        WindowTitleHint = 2
        WindowSystemMenuHint = 4
        WindowMinimizeButtonHint = 8
        WindowMaximizeButtonHint = 16
        WindowCloseButtonHint = 32
        FramelessWindowHint = 64

    class FakeQt:
        WindowType = FakeWindowType

    class FakeQtCore:
        Qt = FakeQt

    flags = standard_window_control_flags(FakeQtCore)

    assert flags & FakeWindowType.WindowTitleHint
    assert flags & FakeWindowType.WindowSystemMenuHint
    assert flags & FakeWindowType.WindowMinimizeButtonHint
    assert flags & FakeWindowType.WindowMaximizeButtonHint
    assert flags & FakeWindowType.WindowCloseButtonHint
    assert not flags & FakeWindowType.FramelessWindowHint

def test_pyside_window_show_opens_main_window_maximized() -> None:
    window = pyside_interview_app.PySideInterviewWindow.__new__(pyside_interview_app.PySideInterviewWindow)
    calls: list[str] = []

    class FakeWindow:
        def showMaximized(self) -> None:
            calls.append("showMaximized")

    window.window = FakeWindow()
    window._fit_window_to_available_screen = lambda: calls.append("fit")
    window._schedule_startup_notifications = lambda: calls.append("schedule_notifications")
    window._schedule_recording_interface_preload = lambda: calls.append("schedule_recording_preload")

    window.show()

    assert calls == ["fit", "showMaximized", "schedule_notifications", "schedule_recording_preload"]

def test_pyside_window_schedules_startup_notifications_once_after_show() -> None:
    window = pyside_interview_app.PySideInterviewWindow.__new__(pyside_interview_app.PySideInterviewWindow)
    calls: list[str] = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay_ms: int, callback) -> None:
            calls.append(f"timer:{delay_ms}")
            callback()

    class FakeQtCore:
        QTimer = FakeTimer

    window.QtCore = FakeQtCore
    window._startup_notifications_scheduled = False
    window._run_due_notifications_safely = lambda: calls.append("notifications")

    window._schedule_startup_notifications()
    window._schedule_startup_notifications()

    assert calls == ["timer:0", "notifications"]

def test_pyside_window_can_defer_secondary_pages_until_navigation(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    built: list[str] = []

    def page(name: str):
        def build(self):
            built.append(name)
            widget = qt_widgets.QWidget()
            widget.setObjectName(f"{name}Page")
            return widget

        return build

    monkeypatch.setattr(pyside_interview_app.PySideInterviewWindow, "_interviews_page", page("Interviews"))
    monkeypatch.setattr(pyside_interview_app.PySideInterviewWindow, "_candidates_page", page("Candidates"))
    monkeypatch.setattr(pyside_interview_app.PySideInterviewWindow, "_admin_page", page("Admin"))
    monkeypatch.setattr(pyside_interview_app.PySideInterviewWindow, "_run_due_notifications_safely", lambda self: None)
    model = pyside_interview_app.InterviewRedesignModel(
        app_title="Test",
        navigation=["Interviews", "Candidates", "Admin"],
        setup_steps=[],
        school_options=[],
        track_labels={},
        readiness_checks=[],
        home=pyside_interview_app.HomeModel(
            primary_action="Start",
            continue_action="Continue",
            admin_visible_on_home=False,
            recent_interviews=[],
            history_rows=[],
        ),
        flows={},
        rubric={},
        history_path=tmp_path / "history.json",
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    assert built == ["Interviews"]
    assert window.stack.count() == 3
    assert window.stack.widget(0).objectName() == "InterviewsPage"

    window._select_main_nav_row(2)
    window._select_main_nav_row(2)

    assert built == ["Interviews", "Admin"]
    assert window.stack.currentWidget().objectName() == "AdminPage"
    window.window.close()
    app.processEvents()

def test_pyside_window_fit_keeps_maximized_state_intact() -> None:
    window = pyside_interview_app.PySideInterviewWindow.__new__(pyside_interview_app.PySideInterviewWindow)

    class FakeWindow:
        def __init__(self) -> None:
            self.maximum_size_calls = 0
            self.resize_calls = 0
            self.move_calls = 0

        def isMaximized(self) -> bool:
            return True

        def isFullScreen(self) -> bool:
            return False

        def screen(self):
            raise AssertionError("maximized windows should not be clamped")

        def setMaximumSize(self, *_args) -> None:
            self.maximum_size_calls += 1

        def resize(self, *_args) -> None:
            self.resize_calls += 1

        def move(self, *_args) -> None:
            self.move_calls += 1

    fake_window = FakeWindow()
    window.window = fake_window

    window._fit_window_to_available_screen()

    assert fake_window.maximum_size_calls == 0
    assert fake_window.resize_calls == 0
    assert fake_window.move_calls == 0

def test_pyside_window_fit_does_not_cap_normal_window_maximum_size() -> None:
    window = pyside_interview_app.PySideInterviewWindow.__new__(pyside_interview_app.PySideInterviewWindow)

    class FakeRect:
        def __init__(self, width: int, height: int) -> None:
            self._width = width
            self._height = height

        def width(self) -> int:
            return self._width

        def height(self) -> int:
            return self._height

        def x(self) -> int:
            return 0

        def y(self) -> int:
            return 0

        def right(self) -> int:
            return self._width - 1

        def bottom(self) -> int:
            return self._height - 1

    class FakeScreen:
        def availableGeometry(self) -> FakeRect:
            return FakeRect(1200, 800)

    class FakeWindow:
        def __init__(self) -> None:
            self.maximum_size_calls = 0
            self.resize_calls = 0
            self.move_calls = 0
            self._geometry = FakeRect(1180, 760)

        def isMaximized(self) -> bool:
            return False

        def isFullScreen(self) -> bool:
            return False

        def screen(self) -> FakeScreen:
            return FakeScreen()

        def setMaximumSize(self, *_args) -> None:
            self.maximum_size_calls += 1

        def width(self) -> int:
            return 1180

        def height(self) -> int:
            return 760

        def resize(self, *_args) -> None:
            self.resize_calls += 1

        def geometry(self) -> FakeRect:
            return self._geometry

        def move(self, *_args) -> None:
            self.move_calls += 1

    fake_window = FakeWindow()
    window.window = fake_window

    window._fit_window_to_available_screen()

    assert fake_window.maximum_size_calls == 0
    assert fake_window.resize_calls == 0
    assert fake_window.move_calls == 1

def test_pyside_history_generate_offer_button_prefills_offer_wizard(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "outcome": "Hire",
            "offer_status": "not_generated",
        }
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Candidates")

    button = next(
        child
        for child in window.window.findChildren(qt_widgets.QPushButton)
        if child.text() == "Generate Offer" and child.property("history_row_key") == "hist-1"
    )
    assert button.isEnabled()

    button.click()
    app.processEvents()

    assert window.stack.currentIndex() == 2
    assert window.offer_fields["candidate"].text() == "Latoya Nugent"
    assert window.offer_fields["school"].text() == "Palmdale"
    assert window.offer_fields["position"].text() == "Preschool Teacher"
    window.window.close()
    app.processEvents()

def test_pyside_history_offer_generation_updates_history_status(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "outcome": "Hire",
            "offer_status": "not_generated",
        }
    )
    template_path = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("[First Name] [Last Name] | [City] | [Position]")
    doc.save(template_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Offers")
    notifications = []

    class FakeNotifications:
        def emit_event(self, event_type, payload, idempotency_key):
            notifications.append((event_type, payload, idempotency_key))
            return []

    window.notification_service = FakeNotifications()
    window._open_history_offer(model.home.history_rows[0])
    window.offer_fields["template_path"].setText(str(template_path))
    window.offer_fields["output_dir"].setText(str(tmp_path / "offers"))
    window.offer_fields["start_date"].setText("2026-06-23")
    window.offer_fields["hourly_pay"].setText("22.50")
    window.offer_fields["hours_week"].setText("40")

    window._generate_offer_from_fields()

    rows = InterviewHistoryStore(history_path).load()
    assert rows[0]["offer_status"] == "generated"
    assert notifications[0][0] == "offer.generated"
    assert notifications[0][1]["candidate_name"] == "Latoya Nugent"
    expected_name = f"{date.today().isoformat()} - Offer - Latoya_Nugent.docx"
    assert rows[0]["offer_letter_path"].endswith(expected_name)
    assert "Offer generated:" in window.offer_status_label.text()
    window.window.close()
    app.processEvents()

def test_pyside_rating_notification_emits_for_hire_and_borderline_only(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    notifications = []

    class FakeNotifications:
        def emit_event(self, event_type, payload, idempotency_key):
            notifications.append((event_type, payload, idempotency_key))
            return []

    window.notification_service = FakeNotifications()
    window.session = SimpleNamespace(
        candidate_name="Jane Doe",
        school="Palmdale",
        position="Teacher",
        interview_date="2026-07-02",
        qualification=CandidateQualification(
            has_degree=True,
            degree_type="BA",
            degree_in_ece=False,
            ece_units_completed=18,
            years_experience=4,
        ),
    )

    window._emit_pyside_rating_notification({"scoring": {"outcome": "Hire", "percent_of_max_label": "85%"}, "history_id": "hist-1"})
    window._emit_pyside_rating_notification({"scoring": {"outcome": "Borderline", "percent_of_max": 70}, "history_id": "hist-2"})
    window._emit_pyside_rating_notification({"scoring": {"outcome": "No Hire", "percent_of_max": 50}, "history_id": "hist-3"})

    assert [event[0] for event in notifications] == ["interview.rating.hire", "interview.rating.borderline"]
    assert notifications[0][1]["candidate_name"] == "Jane Doe"
    assert notifications[0][1]["score"] == "85%"
    assert notifications[0][1]["degree_type"] == "BA"
    assert notifications[0][1]["ece_units_completed"] == "18"
    assert notifications[0][1]["years_experience"] == "4"
    assert notifications[1][2] == "hist-2:interview.rating.borderline"
    window.window.close()
    app.processEvents()

def test_pyside_live_question_wraps_scores_inside_vertical_scroll_area(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    model = build_interview_redesign_model(
        rubric_path=_write_long_descriptor_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)

    window.session_index = 1
    window._render_live_question_page()

    scroll_areas = window.interview_tabs.widget(2).findChildren(qt_widgets.QScrollArea)
    score_labels = window.interview_tabs.widget(2).findChildren(qt_widgets.QLabel, "ScoreOptionText")

    assert scroll_areas
    assert scroll_areas[0].widgetResizable()
    assert scroll_areas[0].horizontalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert score_labels
    assert all(label.wordWrap() for label in score_labels)
    assert any("consent-oriented strategies" in label.text() for label in score_labels)
    window.window.close()
    app.processEvents()

def test_pyside_contract_documents_supported_desktop_surface() -> None:
    contract_text = Path("contracts/pyside_interview_app.contract.yaml").read_text(encoding="utf-8")
    required_categories = [
        "pyside_desktop_surface",
        "interview finalize/history",
        "onboarding",
        "Admin Studio",
        "notification editing",
        "runtime_wrapper",
        "setup_and_run",
    ]

    for category in required_categories:
        assert category in contract_text


def _write_test_rubric(tmp_path: Path) -> Path:
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "metadata": {"version": "test"},
                "scoring": {},
                "tracks": {"preschool": {"label": "Preschool", "max_weighted_total": 5}},
                "absolute_disqualifiers": ["Unsafe handling"],
                "traits": [
                    {
                        "id": "trait_1",
                        "name": "Empathy",
                        "priority": "Critical",
                        "weight": 1,
                        "applicable_tracks": ["preschool"],
                        "primary_question": "Tell me about a hard child moment.",
                        "descriptors": {"1": "Concern", "2": "Weak", "3": "Mixed", "4": "Strong", "5": "Excellent"},
                        "sample_answers": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return rubric_path


def _write_long_descriptor_rubric(tmp_path: Path) -> Path:
    rubric_path = _write_test_rubric(tmp_path)
    payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    payload["traits"][0]["descriptors"]["4"] = (
        "Acknowledges the child's discomfort and follows safety rules, uses respectful language "
        "but may not fully describe consent-oriented strategies, focuses on keeping the child "
        "safe while trying to respond calmly and professionally."
    )
    rubric_path.write_text(json.dumps(payload), encoding="utf-8")
    return rubric_path


def _docx_text(path: Path) -> str:
    doc = Document(path)
    chunks = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            chunks.extend(cell.text for cell in row.cells)
    return "\n".join(chunks)

def test_pyside_staffing_dashboard_imports_seed_and_shows_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "dont_need_now"},
                                    {"position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now"},
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = _pyside_window_on_page(model, "Staffing")
    labels = [label.text() for label in window.stack.widget(3).findChildren(qt_widgets.QLabel)]
    table = window.window.findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")

    assert any("Open positions: 1" in text for text in labels)
    assert table is not None
    assert table.rowCount() == 2
    assert table.horizontalHeaderItem(2).text() == "Person"
    assert table.horizontalHeaderItem(5).text() == "Permit Status"
    assert table.horizontalHeaderItem(6).text() == "Details"
    assert table.horizontalHeaderItem(7).text() == "Action"
    assert table.item(1, 1).text() == "Tranquility"
    assert table.item(1, 2).text() == "OPEN POSITION"
    assert table.item(1, 3).text() == "need_now"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_dashboard_renders_parallel_main_dashboard_without_mutating_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"},
                                    {
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Imgard", "permit_status": "permit_in_process"},
                                    },
                                ],
                            },
                            {
                                "name": "Tranquility",
                                "program": "Preschool",
                                "licensed_capacity": 18,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "coming", "person": {"name": "James"}},
                                ],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    before_count = len(store.list_assignments())
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    assert "Staffing" in nav_items
    assert "Staffing v2" in nav_items
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()

    assert window.sidebar_panel.isHidden()
    assert page.findChild(qt_widgets.QFrame, "StaffingV2Shell") is not None
    assert "QScrollBar:vertical" in page.styleSheet()
    assert "QScrollBar::handle:vertical" in page.styleSheet()
    staffing_sidebar = page.findChild(qt_widgets.QFrame, "StaffingV2Sidebar")
    assert staffing_sidebar is not None
    assert not staffing_sidebar.isHidden()
    sidebar_text = _widget_text(staffing_sidebar)
    assert "Admin Studio" not in sidebar_text
    assert "Launch Pad Learning" in sidebar_text
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2DashboardNavButton").text() == "Staffing Dashboard"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2DashboardNavButton").property("staffingV2ActiveNav") is True
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2HomeNavButton").text() == "Dashboard"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleNavButton").text() == "People"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryNavButton").text() == "Assignment History"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2AnalyticsNavButton").text() == "Analytics"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsNavButton").text() == "Notifications"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2IntegrationsNavButton").text() == "Integrations"
    for object_name in (
        "StaffingV2HomeNavButton",
        "StaffingV2DashboardNavButton",
        "StaffingV2ClassroomsNavButton",
        "StaffingV2PeopleNavButton",
        "StaffingV2HistoryNavButton",
        "StaffingV2AnalyticsNavButton",
        "StaffingV2NotificationsNavButton",
        "StaffingV2ValidationNavButton",
        "StaffingV2IntegrationsNavButton",
        "StaffingV2SettingsNavButton",
    ):
        assert not page.findChild(qt_widgets.QPushButton, object_name).icon().isNull()
    for object_name in (
        "StaffingV2HomeNavButton",
        "StaffingV2AnalyticsNavButton",
        "StaffingV2IntegrationsNavButton",
        "StaffingV2SettingsNavButton",
    ):
        assert not page.findChild(qt_widgets.QPushButton, object_name).isEnabled()
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsNavButton").isEnabled()
    assert page.findChild(qt_widgets.QFrame, "StaffingV2TopTabBar") is None
    header_top_row = page.findChild(qt_widgets.QFrame, "StaffingV2DashboardHeaderTopRow")
    summary_action_row = page.findChild(qt_widgets.QFrame, "StaffingV2DashboardSummaryActionRow")
    assert header_top_row is not None
    assert summary_action_row is not None
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2SchoolFilter").parent() is header_top_row
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ProgramFilter").parent() is header_top_row
    assert page.findChild(qt_widgets.QLineEdit, "StaffingV2Search").parent() is header_top_row
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").parent() is header_top_row
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ExportButton").parent() is summary_action_row
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ViewHistoryButton").parent() is summary_action_row
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ExportButton").text() == "Export"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ViewHistoryButton").text() == "View History"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").text() == "Add Position"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").minimumHeight() >= 40
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2ExportButton").icon().isNull()
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2ViewHistoryButton").icon().isNull()
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").icon().isNull()
    export_button = page.findChild(qt_widgets.QPushButton, "StaffingV2ExportButton")
    assert export_button.isEnabled()
    export_button.click()
    app.processEvents()
    export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2DashboardExportDialog")
    assert export_dialog is not None
    export_text = _widget_text(export_dialog)
    assert "Export Staffing Dashboard" in export_text
    assert "Schools 1" in export_text
    assert "School filter Hawthorne" in export_text
    assert "Program filter All Programs" in export_text
    assert "Permit issues 1" in export_text
    assert "Open positions 1" in export_text
    assert "Harmony 1" in export_text
    export_dialog.accept()
    app.processEvents()
    assert _icon_has_primary_blue(page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").icon())
    assert page.findChild(qt_widgets.QFrame, "StaffingV2Sidebar").minimumWidth() >= 240
    assert page.objectName() == "PySideStaffingV2Page"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2PageTitle").text() == "Staffing Dashboard"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2SchoolFilter").currentText() == "Hawthorne"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ProgramFilter").currentText() == "All Programs"
    assert page.findChild(qt_widgets.QLineEdit, "StaffingV2Search").placeholderText() == "Search classrooms"
    assert page.findChild(qt_widgets.QLineEdit, "StaffingV2Search").actions()
    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2MetricCard"))
    assert "Schools: 1" in metric_text
    assert "Open positions: 1" in metric_text
    assert "Avg fill time:" in metric_text
    assert "Open > 7 days:" in metric_text
    assert "Validation: 3 issues" in metric_text
    assert "20639" not in metric_text
    summary_chips = page.findChildren(qt_widgets.QFrame, "StaffingV2MetricCard")
    assert all(card.parent() is summary_action_row for card in summary_chips)
    chip_variants = {chip.accessibleName(): chip.property("staffingV2SummaryVariant") for chip in summary_chips}
    assert chip_variants["Schools: 1"] == "info"
    assert chip_variants["Open positions: 1"] == "info"
    assert chip_variants["Open > 7 days: 0"] == "danger"
    assert chip_variants["Validation: 3 issues"] == "danger"
    for card in summary_chips:
        assert card.maximumHeight() <= 50
        icon = card.findChild(qt_widgets.QLabel, "StaffingV2SummaryIcon")
        assert icon is not None
        assert icon.pixmap() is not None
        assert not icon.pixmap().isNull()
    classroom_list = page.findChild(qt_widgets.QListWidget, "StaffingV2ClassroomList")
    detail_scroll = page.findChild(qt_widgets.QScrollArea, "StaffingV2DashboardDetailScroll")
    assert classroom_list.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert classroom_list.horizontalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert detail_scroll is not None
    assert detail_scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert detail_scroll.horizontalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert detail_scroll.widget().objectName() == "StaffingV2DashboardDetailContent"
    list_filter = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomListFilterButton")
    assert list_filter is not None
    assert list_filter.text() == ""
    assert list_filter.isEnabled()
    assert not list_filter.icon().isNull()
    assert list_filter.toolTip() == "Classroom filters"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomListFooter").text() == (
        "Showing 1-2 of 2 classrooms"
    )
    assert classroom_list.count() == 2
    assert "Harmony 1" in classroom_list.item(0).text()
    assert "Need 1" in classroom_list.item(0).text()
    assert classroom_list.item(0).sizeHint().height() >= 60
    first_row_widget = classroom_list.itemWidget(classroom_list.item(0))
    assert first_row_widget is not None
    assert first_row_widget.objectName() == "StaffingV2ClassroomListItem"
    assert first_row_widget.testAttribute(qt_core.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert first_row_widget.findChild(qt_widgets.QFrame, "StaffingV2ClassroomStatusDot") is not None
    assert first_row_widget.findChild(qt_widgets.QFrame, "StaffingV2ClassroomStatusDot").property("staffingV2Status") == "need_now"
    assert first_row_widget.findChild(qt_widgets.QLabel, "StaffingV2ClassroomItemTitle").text() == "Harmony 1"
    assert first_row_widget.findChild(qt_widgets.QLabel, "StaffingV2ClassroomItemTitle").testAttribute(
        qt_core.Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )
    assert first_row_widget.findChild(qt_widgets.QLabel, "StaffingV2ClassroomItemCounts").text() == (
        "Need 1 · Replace 0 · Coming 0 · Filled 1 · Don't Need 0"
    )
    assert first_row_widget.findChild(qt_widgets.QLabel, "StaffingV2ClassroomItemChevron").text() == ">"
    list_filter.click()
    app.processEvents()
    filter_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2DashboardClassroomFilterDrawer")
    assert filter_dialog is not None
    assert "Classroom Filters" in _widget_text(filter_dialog)
    status_filter = filter_dialog.findChild(qt_widgets.QComboBox, "StaffingV2DashboardClassroomStatusFilter")
    status_filter.setCurrentText("Coming")
    filter_dialog.findChild(qt_widgets.QPushButton, "StaffingV2FilterApplyButton").click()
    app.processEvents()
    assert classroom_list.count() == 1
    assert "Tranquility" in classroom_list.item(0).text()
    export_button.click()
    app.processEvents()
    filtered_export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2DashboardExportDialog")
    assert filtered_export_dialog is not None
    filtered_export_text = _widget_text(filtered_export_dialog)
    assert "Classroom status filter Coming" in filtered_export_text
    assert "Tranquility" in filtered_export_text
    assert "Harmony 1" not in filtered_export_text
    filtered_export_dialog.accept()
    app.processEvents()
    filter_dialog = None
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomListFilterButton").click()
    app.processEvents()
    filter_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2DashboardClassroomFilterDrawer")
    filter_dialog.findChild(qt_widgets.QPushButton, "StaffingV2FilterResetButton").click()
    filter_dialog.findChild(qt_widgets.QPushButton, "StaffingV2FilterApplyButton").click()
    app.processEvents()
    assert classroom_list.count() == 2
    classroom_list.setCurrentRow(0)
    app.processEvents()
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomTitle").text() == "Harmony 1"
    overview_cards = page.findChildren(qt_widgets.QFrame, "StaffingV2OverviewCard")
    assert len(overview_cards) == 6
    for overview_card in overview_cards:
        icon = overview_card.findChild(qt_widgets.QLabel, "StaffingV2CardIcon")
        assert icon is not None
        assert icon.pixmap() is not None
        assert not icon.pixmap().isNull()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert table.maximumHeight() <= 230
    assert table.columnCount() == 8
    assert [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())] == [
        "",
        "Position",
        "Person",
        "Status",
        "Start Date",
        "Days Open",
        "Permit Status",
        "Next Action",
    ]
    assert table.verticalHeader().isHidden()
    assert table.horizontalHeader().sectionResizeMode(0) == qt_widgets.QHeaderView.ResizeMode.Fixed
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    table_widget_text = {
        table.cellWidget(row, column).text().strip()
        if hasattr(table.cellWidget(row, column), "text")
        else _widget_text(table.cellWidget(row, column)).strip()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.cellWidget(row, column) is not None
    }
    table_text |= table_widget_text
    assert {"Teacher 1", "OPEN POSITION", "Need Now", "Teacher 2", "Imgard", "Filled"} <= table_text
    assert "20639" not in table_text
    assert table.editTriggers() == qt_widgets.QAbstractItemView.EditTrigger.NoEditTriggers
    need_now_row = _staffing_row_for_position(table, "Teacher 1")
    filled_row = _staffing_row_for_position(table, "Teacher 2")
    assert table.item(need_now_row, 0).text() == "1"
    assert table.item(filled_row, 0).text() == "2"
    assert table.cellWidget(need_now_row, 3).objectName() == "StaffingV2NeedNowChip"
    assert table.cellWidget(filled_row, 3).objectName() == "StaffingV2FilledChip"
    assert table.cellWidget(need_now_row, 6).objectName() == "StaffingV2NeutralChip"
    assert table.cellWidget(filled_row, 6).objectName() == "StaffingV2ComingChip"
    for chip in (
        table.cellWidget(need_now_row, 3),
        table.cellWidget(filled_row, 3),
        table.cellWidget(need_now_row, 6),
        table.cellWidget(filled_row, 6),
    ):
        assert chip.findChild(qt_widgets.QLabel, "StaffingV2ChipIcon") is not None
        assert chip.findChild(qt_widgets.QLabel, "StaffingV2ChipText") is not None
    assert table.item(need_now_row, 3) is None
    assert table.item(filled_row, 6) is None
    need_now_action = table.cellWidget(need_now_row, table.columnCount() - 1)
    filled_action = table.cellWidget(filled_row, table.columnCount() - 1)
    assert need_now_action.text() == "Mark Coming"
    assert need_now_action.menu() is not None
    assert [action.text() for action in need_now_action.menu().actions()] == [
        "Mark Coming",
        "Mark Not Needed",
        "Delete Position",
        "View Details",
    ]
    assert filled_action.text() == "Manage Filled"
    assert filled_action.menu() is not None
    assert [action.text() for action in filled_action.menu().actions()] == ["Manage Filled", "Replace", "Update Permit", "View Details"]
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").text() == "Add Position"
    drop_zone = page.findChild(qt_widgets.QFrame, "StaffingV2AddPositionDropZone")
    assert drop_zone is not None
    drop_zone_button = drop_zone.findChild(qt_widgets.QPushButton, "StaffingV2DropZoneAddButton")
    assert drop_zone_button.text() == "Add Position"
    assert not drop_zone_button.icon().isNull()
    priority_chip = page.findChild(qt_widgets.QFrame, "StaffingV2PriorityChip")
    assert priority_chip is not None
    assert priority_chip.findChild(qt_widgets.QLabel, "StaffingV2PriorityChipIcon") is not None
    assert priority_chip.findChild(qt_widgets.QLabel, "StaffingV2PriorityChipText").text() == "Need Now"
    assert "Need Now" in _widget_text(page.findChild(qt_widgets.QFrame, "StaffingV2StatusKey"))
    assert len(store.list_assignments()) == before_count
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_dashboard_scrollbars_have_scrollable_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    classrooms = [
        {
            "name": f"Classroom {index:02d}",
            "program": "Preschool",
            "licensed_capacity": 18,
            "slots": [
                {
                    "position_name": "Teacher 1",
                    "position_type": "Teacher",
                    "status": "filled",
                    "person": {"name": f"Teacher {index}"},
                }
            ],
        }
        for index in range(1, 18)
    ]
    seed_path.write_text(json.dumps({"schools": [{"name": "Hawthorne", "classrooms": classrooms}]}), encoding="utf-8")
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.window.resize(1280, 700)
    window.window.show()
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    classroom_list = page.findChild(qt_widgets.QListWidget, "StaffingV2ClassroomList")
    detail_scroll = page.findChild(qt_widgets.QScrollArea, "StaffingV2DashboardDetailScroll")

    assert classroom_list.count() == 17
    assert classroom_list.verticalScrollBar().maximum() > 0
    classroom_list.verticalScrollBar().setValue(classroom_list.verticalScrollBar().maximum())
    assert classroom_list.verticalScrollBar().value() == classroom_list.verticalScrollBar().maximum()
    assert detail_scroll.verticalScrollBar().maximum() > 0
    detail_scroll.verticalScrollBar().setValue(detail_scroll.verticalScrollBar().maximum())
    assert detail_scroll.verticalScrollBar().value() == detail_scroll.verticalScrollBar().maximum()
    scroll_areas = page.findChildren(qt_widgets.QAbstractScrollArea)
    assert scroll_areas
    for scroll_area in scroll_areas:
        assert scroll_area.sizeAdjustPolicy() == qt_widgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        assert scroll_area.verticalScrollBar().singleStep() == 24
        if isinstance(scroll_area, qt_widgets.QAbstractItemView):
            assert scroll_area.verticalScrollMode() == qt_widgets.QAbstractItemView.ScrollMode.ScrollPerPixel
            assert scroll_area.horizontalScrollMode() == qt_widgets.QAbstractItemView.ScrollMode.ScrollPerPixel

    classroom_list.verticalScrollBar().setValue(0)
    list_center = classroom_list.viewport().rect().center()
    classroom_wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(list_center),
        qt_core.QPointF(classroom_list.viewport().mapToGlobal(list_center)),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(classroom_list.viewport(), classroom_wheel)
    assert classroom_list.verticalScrollBar().value() > 0

    classroom_list.verticalScrollBar().setValue(0)
    routed_item_wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(page.mapFromGlobal(classroom_list.viewport().mapToGlobal(list_center))),
        qt_core.QPointF(classroom_list.viewport().mapToGlobal(list_center)),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(page, routed_item_wheel)
    assert classroom_list.verticalScrollBar().value() > 0

    classroom_list.verticalScrollBar().setValue(0)
    window_item_wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(window.window.mapFromGlobal(classroom_list.viewport().mapToGlobal(list_center))),
        qt_core.QPointF(classroom_list.viewport().mapToGlobal(list_center)),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(window.window, window_item_wheel)
    assert classroom_list.verticalScrollBar().value() > 0

    detail_scroll.verticalScrollBar().setValue(0)
    detail_target = detail_scroll.widget().findChildren(qt_widgets.QLabel)[-1]
    detail_center = detail_target.rect().center()
    detail_wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(detail_center),
        qt_core.QPointF(detail_target.mapToGlobal(detail_center)),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(detail_target, detail_wheel)
    assert detail_scroll.verticalScrollBar().value() > 0

    detail_scroll.verticalScrollBar().setValue(0)
    window_detail_wheel = qt_gui.QWheelEvent(
        qt_core.QPointF(window.window.mapFromGlobal(detail_target.mapToGlobal(detail_center))),
        qt_core.QPointF(detail_target.mapToGlobal(detail_center)),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    app.sendEvent(window.window, window_detail_wheel)
    assert detail_scroll.verticalScrollBar().value() > 0

    assignment_id = store.list_assignments()[0].id
    window.staffing_v2_dashboard._show_position_drawer(assignment_id)
    for index in range(24):
        window.staffing_v2_dashboard.drawer_layout.addWidget(qt_widgets.QLabel(f"Wheel relay row {index}"))
    window.staffing_v2_dashboard.drawer_panel.show_overlay()
    app.processEvents()
    drawer_scroll = page.findChild(qt_widgets.QScrollArea, "StaffingV2PositionDrawerScroll")
    drawer_scroll.widget().setMinimumHeight(1400)
    window.staffing_v2_dashboard.drawer_panel.show_overlay()
    app.processEvents()
    assert drawer_scroll.verticalScrollBar().maximum() > 0
    wheel_target = drawer_scroll.widget().findChildren(qt_widgets.QLabel)[-1]
    drawer_scroll.verticalScrollBar().setValue(0)
    center = wheel_target.rect().center()
    wheel_event = qt_gui.QWheelEvent(
        qt_core.QPointF(center),
        qt_core.QPointF(wheel_target.mapToGlobal(center)),
        qt_core.QPoint(0, 0),
        qt_core.QPoint(0, -120),
        qt_core.Qt.MouseButton.NoButton,
        qt_core.Qt.KeyboardModifier.NoModifier,
        qt_core.Qt.ScrollPhase.ScrollUpdate,
        False,
    )

    app.sendEvent(wheel_target, wheel_event)

    assert drawer_scroll.verticalScrollBar().value() > 0

    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_full_app_selection_resyncs_shared_page_scrollbars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": f"Classroom {index:02d}",
                                "positions": [
                                    {
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": f"Teacher {index}"},
                                    }
                                ],
                            }
                            for index in range(1, 14)
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    staffing_index = nav_items.index("Staffing v2")
    window.sidebar.setCurrentRow(staffing_index)
    app.processEvents()
    dashboard = window.staffing_v2_dashboard
    assert isinstance(dashboard, StaffingDashboardV2Page)
    assert dashboard.school_filter == ""
    page = window.stack.currentWidget()
    detail_scroll = page.findChild(qt_widgets.QScrollArea, "StaffingV2DashboardDetailScroll")
    assert detail_scroll.verticalScrollBar().maximum() > 0

    sync_calls: list[str] = []
    original_sync = dashboard._sync_staffing_v2_scroll_ranges

    def sync_spy() -> None:
        sync_calls.append("sync")
        original_sync()

    dashboard._sync_staffing_v2_scroll_ranges = sync_spy
    window.sidebar.setCurrentRow(nav_items.index("Interviews"))
    app.processEvents()
    window.sidebar.setCurrentRow(staffing_index)
    app.processEvents()

    assert sync_calls == ["sync"]
    assert isinstance(window.staffing_v2_dashboard, StaffingDashboardV2Page)
    assert window.stack.currentWidget() is page
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_notifications_nav_opens_rule_dashboard_and_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    saved = NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assignment.need_now",
            label="Hiring manager: position needed now",
            subject_template="Position needed now: {position_name}",
            body_template="Hi {hiring_manager_name},\n\nPlease review {position_name}.",
            recipients=[
                NotificationRecipient(
                    email="director@example.com",
                    name="Director",
                    role_label="Director",
                )
            ],
            active=True,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    page = window.stack.currentWidget()
    notifications_nav = page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsNavButton")

    assert notifications_nav.isEnabled()
    notifications_nav.click()
    app.processEvents()

    assert page.findChild(qt_widgets.QWidget, "StaffingV2NotificationsDashboard") is not None
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsNavButton").property("staffingV2ActiveNav") is True
    assert page.findChild(qt_widgets.QLabel, "StaffingV2NotificationsTitle").text() == "Notifications"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2NotificationsRuleCount").text().endswith(" rules")
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEventFilter").currentText() == "All events"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEnabledFilter").currentText() == "All statuses"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsCreateButton").text() == "Create / Modify"
    list_widget = page.findChild(qt_widgets.QListWidget, "StaffingV2NotificationsRuleList")
    assert list_widget.count() >= 1
    list_text = "\n".join(list_widget.item(index).text() for index in range(list_widget.count()))
    assert "Hiring manager: position needed now" in list_text
    assert "staffing.assignment.need_now" in list_text

    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEventFilter").setCurrentText(saved.event_type)
    app.processEvents()
    assert page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationRuleLabel").text() == saved.label
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationRuleEvent").currentText() == saved.event_type
    assert page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationSubject").text() == saved.subject_template
    assert page.findChild(qt_widgets.QPlainTextEdit, "StaffingV2NotificationBody").toPlainText() == saved.body_template
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsRecipientsFilter").currentText() == "All recipients"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsTemplateFilter").currentText() == "All templates"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsSort").currentText() == "Event sort"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsViewToggle").currentText() == "List"
    assert "Director <director@example.com>" in _widget_text(
        page.findChild(qt_widgets.QFrame, "StaffingV2NotificationRecipientChips")
    )
    assert "{position_name}" in page.findChild(qt_widgets.QLabel, "StaffingV2NotificationVariablesPreview").text()
    assert "No issues found" in page.findChild(qt_widgets.QLabel, "StaffingV2NotificationValidation").text()
    assert "Pending scheduled:" in page.findChild(qt_widgets.QLabel, "StaffingV2NotificationAuditSummary").text()

    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_notifications_filters_chips_preview_and_test_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text('{"schools":[]}', encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    store = NotificationStore(notification_rules_path)
    saved = store.save_rule(
        NotificationRule(
            event_type="staffing.assignment.need_now",
            label="Need now",
            subject_template="Need now: {position_name}",
            body_template="Please review {position_name} at {school}.",
            recipients=[NotificationRecipient(email="director@example.com", role_label="Director")],
            active=False,
        )
    )
    store.save_rule(
        NotificationRule(
            event_type="custom.missing",
            label="Missing template",
            subject_template="",
            body_template="",
            recipients=[],
            active=True,
        )
    )

    class FakeNotificationService:
        def send_test(self, rule_id: int, payload: dict[str, str], idempotency_key: str) -> NotificationSendResult:
            NotificationStore(notification_rules_path).record_send_attempt(
                event_type="staffing.assignment.need_now.test",
                rule_id=rule_id,
                idempotency_key=idempotency_key,
                recipient_count=1,
                status="sent",
            )
            return NotificationSendResult(
                event_type="staffing.assignment.need_now.test",
                rule_id=rule_id,
                status="sent",
                recipient_count=1,
            )

    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    monkeypatch.setattr(
        pyside_interview_app,
        "notification_service_from_email_account_settings",
        lambda **_kwargs: FakeNotificationService(),
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    page = window.stack.currentWidget()
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsNavButton").click()
    app.processEvents()

    list_widget = page.findChild(qt_widgets.QListWidget, "StaffingV2NotificationsRuleList")
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEnabledFilter").setCurrentText("Disabled")
    app.processEvents()
    assert "Need now" in "\n".join(list_widget.item(index).text() for index in range(list_widget.count()))
    assert "Missing template" not in "\n".join(list_widget.item(index).text() for index in range(list_widget.count()))
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsTemplateFilter").setCurrentText("Missing subject")
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEnabledFilter").setCurrentText("All statuses")
    app.processEvents()
    assert list_widget.count() == 1
    assert "Missing template" in list_widget.item(0).text()
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsClearFilters").click()
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsSort").setCurrentText("Recipients sort")
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsViewToggle").setCurrentText("Grid")
    app.processEvents()
    assert list_widget.property("staffingV2NotificationViewMode") == "grid"

    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEventFilter").setCurrentText(saved.event_type)
    app.processEvents()
    recipients = page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationRecipients")
    recipients.setText("HR <hr@example.com>")
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationRecipientAdd").click()
    app.processEvents()
    chips_text = _widget_text(page.findChild(qt_widgets.QFrame, "StaffingV2NotificationRecipientChips"))
    assert "HR <hr@example.com>" in chips_text
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationRecipientRemove_hr_example_com").click()
    app.processEvents()
    assert "HR <hr@example.com>" not in _widget_text(page.findChild(qt_widgets.QFrame, "StaffingV2NotificationRecipientChips"))
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationRecipientHiringManager").click()
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationRecipientDirector").click()
    app.processEvents()
    chips_text = _widget_text(page.findChild(qt_widgets.QFrame, "StaffingV2NotificationRecipientChips"))
    assert "Hiring Manager <recruiting@launchpadpreschool.com>" in chips_text
    assert "Director (school-based)" in chips_text

    subject = page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationSubject")
    subject.setCursorPosition(len(subject.text()))
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationSubjectVariable_position_name").click()
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationBodyBold").click()
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationVariable_school").click()
    assert "{position_name}" in subject.text()
    assert "{school}" in page.findChild(qt_widgets.QPlainTextEdit, "StaffingV2NotificationBody").toPlainText()

    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationPreview").click()
    app.processEvents()
    preview = page.findChild(qt_widgets.QDialog, "StaffingV2NotificationPreviewDialog")
    assert preview is not None
    assert "Teacher 1" in _widget_text(preview)
    preview.close()

    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationSendTest").click()
    app.processEvents()
    assert page.findChild(qt_widgets.QLabel, "StaffingV2NotificationsStatus").text() == "Test send sent."
    assert "sent" in page.findChild(qt_widgets.QLabel, "StaffingV2NotificationAuditSummary").text()
    assert NotificationStore(notification_rules_path).get_rule(saved.id).active is False
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationSave").click()
    app.processEvents()
    reloaded = NotificationStore(notification_rules_path).get_rule(saved.id)
    assert [(recipient.recipient_type, recipient.role_key) for recipient in reloaded.recipients if recipient.recipient_type == "role"] == [
        ("role", "hiring_manager"),
        ("role", "director"),
    ]

    recipients.setText("not-an-email")
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationSave").click()
    app.processEvents()
    assert "Invalid recipient email" in page.findChild(qt_widgets.QLabel, "StaffingV2NotificationValidation").text()

    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_notifications_editor_saves_rule_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text('{"schools":[]}', encoding="utf-8")
    notification_rules_path = tmp_path / "notification_rules.sqlite3"
    NotificationStore(notification_rules_path).save_rule(
        NotificationRule(
            event_type="staffing.assignment.need_now",
            label="Hiring manager: position needed now",
            subject_template="Position needed now: {position_name}",
            body_template="Please review {position_name}.",
            recipients=[NotificationRecipient(email="director@example.com", name="Director", role_label="Director")],
            active=True,
        )
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    monkeypatch.setattr(pyside_interview_app, "NOTIFICATION_RULES_PATH", notification_rules_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    page = window.stack.currentWidget()
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationsNavButton").click()
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationsEventFilter").setCurrentText("staffing.assignment.need_now")
    app.processEvents()

    page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationRuleLabel").setText("Director: permit updated")
    event = page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationRuleEvent")
    event.setCurrentText("staffing.permit.updated")
    page.findChild(qt_widgets.QCheckBox, "StaffingV2NotificationEnabled").setChecked(False)
    page.findChild(qt_widgets.QComboBox, "StaffingV2NotificationTiming").setCurrentText("Reference date")
    page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationDateField").setText("start_date")
    page.findChild(qt_widgets.QSpinBox, "StaffingV2NotificationOffsetDays").setValue(2)
    page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationSubject").setText("Permit updated: {person_name}")
    page.findChild(qt_widgets.QPlainTextEdit, "StaffingV2NotificationBody").setPlainText("Permit: {permit_status}")
    page.findChild(qt_widgets.QLineEdit, "StaffingV2NotificationRecipients").setText(
        "Director <director@example.com>, HR <hr@example.com>"
    )
    page.findChild(qt_widgets.QPushButton, "StaffingV2NotificationSave").click()
    app.processEvents()

    rule = next(
        rule
        for rule in NotificationStore(notification_rules_path).list_rules("staffing.permit.updated")
        if rule.label == "Director: permit updated"
    )
    assert rule.label == "Director: permit updated"
    assert rule.active is False
    assert rule.trigger_timing == "date_offset"
    assert rule.date_field == "start_date"
    assert rule.offset_days == 2
    assert rule.subject_template == "Permit updated: {person_name}"
    assert [recipient.email for recipient in rule.recipients] == ["director@example.com", "hr@example.com"]
    assert page.findChild(qt_widgets.QLabel, "StaffingV2NotificationsStatus").text() == "Notification rule saved."

    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_staffing_v2_classrooms_dashboard_uses_new_shell_and_db_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"},
                                    {
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Maria Gonzalez", "permit_status": "teacher_permit_approved"},
                                    },
                                ],
                            },
                            {
                                "name": "Tranquility",
                                "program": "Infant",
                                "licensed_capacity": 18,
                                "slots": [
                                    {
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "James Mitchell", "permit_status": "permit_in_process"},
                                    },
                                    {
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Sofia Ramirez", "permit_status": "no_units_needed"},
                                    },
                                ],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    before_assignments = store.list_assignments()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()

    classrooms_nav = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsNavButton")
    assert classrooms_nav.isEnabled()
    classrooms_nav.click()
    app.processEvents()

    assert window.sidebar_panel.isHidden()
    assert page.findChild(qt_widgets.QFrame, "StaffingV2Shell") is not None
    assert classrooms_nav.property("staffingV2ActiveNav") is True
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2DashboardNavButton").property("staffingV2ActiveNav") is False
    assert page.findChild(qt_widgets.QWidget, "StaffingV2ClassroomManagementDashboard") is not None
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsTitle").text() == "Classroom Management"
    assert "Manage classroom records" in page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsSubtitle").text()
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsAddButton").text() == "Add Classroom"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsAddButton").isEnabled()
    classrooms_export = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsExportButton")
    assert classrooms_export.text() == "Export"
    assert not classrooms_export.icon().isNull()
    assert classrooms_export.isEnabled()
    classrooms_export.click()
    app.processEvents()
    classrooms_export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ClassroomsExportDialog")
    assert classrooms_export_dialog is not None
    classrooms_export_text = _widget_text(classrooms_export_dialog)
    assert "Export Classroom Management" in classrooms_export_text
    assert "Total classrooms 2" in classrooms_export_text
    assert "Open positions 1" in classrooms_export_text
    assert "Harmony 1" in classrooms_export_text
    assert "Tranquility" in classrooms_export_text
    classrooms_export_dialog.accept()
    app.processEvents()
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsAddButton").icon().isNull()
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsSchoolFilter").currentText() == "All Schools"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsProgramFilter").currentText() == "All Programs"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsStatusFilter").currentText() == "All Statuses"
    classrooms_search = page.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsSearch")
    assert classrooms_search.placeholderText() == "Search classrooms..."
    assert classrooms_search.actions()
    classrooms_filters = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsMoreFilters")
    assert classrooms_filters.text() == "Filters 0"
    assert classrooms_filters.property("staffingV2FilterActiveCount") == 0
    assert not classrooms_filters.icon().isNull()
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsClear").text() == "Clear"
    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2ClassroomsMetricCard"))
    assert "Total Classrooms 2" in metric_text
    assert "Active 2" in metric_text
    assert "Avg Licensed Capacity 21.0" in metric_text
    assert "Total Positions 4" in metric_text
    assert "Open Positions 1" in metric_text
    assert len(page.findChildren(qt_widgets.QFrame, "StaffingV2ClassroomsMetricCard")) == 5
    for card in page.findChildren(qt_widgets.QFrame, "StaffingV2ClassroomsMetricCard"):
        assert card.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsMetricIcon") is not None
        assert card.findChild(qt_widgets.QLabel, "StaffingV2MetricValue") is not None
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ClassroomsTable")
    assert table.rowCount() == 2
    expected_headers = [
        "Classroom",
        "School",
        "Program",
        "Licensed Capacity",
        "Total Positions",
        "Filled",
        "Open",
        "Priority Status",
        "Active",
        "Actions",
    ]
    assert [table.horizontalHeaderItem(index).text() for index in range(table.columnCount())] == expected_headers
    assert table.columnWidth(0) >= 110
    assert table.columnWidth(3) >= 120
    assert table.columnWidth(7) >= 130
    assert table.currentRow() == 0
    assert table.horizontalScrollBar().value() == 0
    assert table.horizontalHeaderItem(0).text() == "Classroom"
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert {
        "Harmony 1",
        "Hawthorne",
        "Preschool",
        "24",
        "2",
        "1",
        "Need Now",
        "Yes",
        "Tranquility",
        "Infant",
        "18",
        "Filled",
    } <= table_text
    assert table.cellWidget(0, 7).objectName() in {"StaffingV2NeedNowChip", "StaffingV2ReplaceChip"}
    assert table.cellWidget(1, 7).text() == "Filled"
    assert table.cellWidget(0, 8).objectName() == "StaffingV2HealthyChip"
    for row in range(table.rowCount()):
        view_button = table.cellWidget(row, 9)
        assert isinstance(view_button, qt_widgets.QPushButton)
        assert view_button.text() == "View"
        assert not view_button.icon().isNull()
        assert view_button.isEnabled()
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsResultCount").text() == "Showing 1 to 2 of 2 classrooms"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsRowsPerPage").currentText() == "10 / page"
    detail = page.findChild(qt_widgets.QFrame, "StaffingV2ClassroomsDetailPanel")
    detail_text = _widget_text(detail)
    assert "Classroom Detail" in detail_text
    assert "Harmony 1" in detail_text
    assert "Staffing Summary" in detail_text
    assert "Current Positions" in detail_text
    assert "Teacher 1" in detail_text
    assert "OPEN POSITION" in detail_text
    detail_scroll = detail.findChild(qt_widgets.QScrollArea, "StaffingV2ClassroomsDetailScroll")
    assert detail_scroll is not None
    assert detail_scroll.widgetResizable()
    assert detail_scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    detail_viewport_width = detail_scroll.viewport().width()
    first_classroom_card = detail.findChild(qt_widgets.QFrame, "StaffingV2ClassroomsDetailCard")
    assert first_classroom_card.y() < 160
    assert first_classroom_card.width() <= detail_viewport_width
    assert detail_scroll.widget().findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsSaveButton") is None
    classroom_close = detail.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsDetailClose")
    assert classroom_close is not None
    assert classroom_close.text() == ""
    assert not classroom_close.icon().isNull()
    assert len(detail.findChildren(qt_widgets.QFrame, "StaffingV2ClassroomsDetailMetricCard")) == 4
    for detail_card in detail.findChildren(qt_widgets.QFrame, "StaffingV2ClassroomsDetailCard"):
        assert detail_card.sizePolicy().verticalPolicy() == qt_widgets.QSizePolicy.Policy.Maximum
    detail_footer = detail.findChild(qt_widgets.QWidget, "StaffingV2ClassroomsDetailFooter")
    assert detail_footer is not None
    assert detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsDetailClose") is None
    deactivate_button = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsDeactivateButton")
    assert deactivate_button.text() == "Deactivate Classroom"
    assert not deactivate_button.icon().isNull()
    assert detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsDeactivateButton") is deactivate_button
    save_button = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsSaveButton")
    assert save_button.text() == "Save Changes"
    assert not save_button.icon().isNull()
    assert save_button.parent() is not detail_scroll.widget()
    assert detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsSaveButton") is save_button
    validation_panel = page.findChild(qt_widgets.QFrame, "StaffingV2ClassroomsValidationPanel")
    assert "Classroom Validation & Health" in _widget_text(validation_panel)
    health_cards = validation_panel.findChildren(qt_widgets.QFrame, "StaffingV2ClassroomsHealthCard")
    assert len(health_cards) == 5
    assert {card.property("staffingV2HealthVariant") for card in health_cards} <= {"success", "warning", "danger"}
    assert "success" in {card.property("staffingV2HealthVariant") for card in health_cards}
    assert len(store.list_assignments()) == len(before_assignments)
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_add_classroom_dialog_creates_classroom_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    before_assignments = store.list_assignments()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsNavButton").click()
    app.processEvents()

    add_classroom = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsAddButton")
    assert add_classroom.isEnabled()
    add_classroom.click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2AddClassroomDialog")
    assert dialog is not None
    assert "Add Classroom" in _widget_text(dialog)
    assert dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddClassroomSchool").currentText() == "Hawthorne"
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddClassroomName").setText("Sunflower")
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddClassroomProgram").setCurrentText("Preschool")
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddClassroomCapacity").setText("18")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2AddClassroomSave").click()
    app.processEvents()

    classrooms = store.list_classrooms()
    created = [classroom for classroom in classrooms if classroom.name == "Sunflower"][0]
    assert created.school == "Hawthorne"
    assert created.program == "Preschool"
    assert created.licensed_capacity == 18
    assert len(store.list_assignments()) == len(before_assignments)
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ClassroomsTable")
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert {"Sunflower", "18", "0", "Don't Need"} <= table_text
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_classrooms_filter_side_panel_filters_rows_without_mutating_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [{"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"}],
                            },
                            {
                                "name": "Tranquility",
                                "program": "Infant",
                                "licensed_capacity": 18,
                                "slots": [
                                    {
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "James Mitchell", "permit_status": "teacher_permit_approved"},
                                    }
                                ],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    before_assignments = store.list_assignments()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsNavButton").click()
    app.processEvents()
    body_splitter = page.findChild(qt_widgets.QSplitter, "StaffingV2ClassroomsBodySplitter")
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ClassroomsTable")
    before_splitter_sizes = body_splitter.sizes()
    before_table_width = table.width()

    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsMoreFilters").click()
    app.processEvents()

    drawer = page.findChild(qt_widgets.QFrame, "StaffingV2ClassroomsFilterDrawer")
    assert drawer is not None
    assert not drawer.isHidden()
    assert drawer.width() >= 420
    assert drawer.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2ClassroomManagementDashboard")
    assert body_splitter.sizes() == before_splitter_sizes
    assert table.width() == before_table_width
    assert table.horizontalScrollBar().value() == 0
    drawer_text = _widget_text(drawer)
    assert "Filters" in drawer_text
    assert "School" in drawer_text
    assert "Program" in drawer_text
    assert "Status" in drawer_text
    assert "Open Positions" in drawer_text
    assert "Days Open" in drawer_text
    assert "Permit Status" in drawer_text
    assert "Assigned Staff" in drawer_text
    assert "Sort By" in drawer_text
    assert drawer.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsFilterSchool").currentText() == "All Schools"
    assert drawer.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsFilterProgram").currentText() == "All Programs"
    assert drawer.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsFilterPermit").currentText() == "All Permit Statuses"
    assert drawer.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsFilterAssignedStaff").currentText() == "All Staff"
    assert drawer.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsFilterSortBy").currentText() == "Default Order"
    assert drawer.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsFilterDaysFrom") is not None
    assert drawer.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsFilterDaysTo") is not None
    reset = drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterReset")
    assert reset.text() == "Reset"
    assert not reset.icon().isNull()
    assert drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterClose").text() == ""
    apply_button = drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterApply")
    assert apply_button.text() == "Apply Filters 0"
    assert apply_button.property("staffingV2FilterActiveCount") == 0
    assert not apply_button.icon().isNull()
    cancel_button = drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterCancel")
    assert not cancel_button.isHidden()
    assert not apply_button.isHidden()
    assert cancel_button.geometry().bottom() <= drawer.rect().bottom()
    assert apply_button.geometry().bottom() <= drawer.rect().bottom()
    need_now = drawer.findChild(qt_widgets.QCheckBox, "StaffingV2ClassroomsFilterNeedNow")
    filled = drawer.findChild(qt_widgets.QCheckBox, "StaffingV2ClassroomsFilterFilled")
    dont_need = drawer.findChild(qt_widgets.QCheckBox, "StaffingV2ClassroomsFilterDontNeed")
    assert need_now.isChecked()
    assert filled.isChecked()
    assert dont_need.isChecked()
    filled.setChecked(False)
    app.processEvents()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ClassroomsTable")
    assert table.rowCount() == 2
    drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterCancel").click()
    app.processEvents()
    assert drawer.isHidden()
    assert table.rowCount() == 2
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsMoreFilters").click()
    app.processEvents()
    filled = drawer.findChild(qt_widgets.QCheckBox, "StaffingV2ClassroomsFilterFilled")
    assert filled.isChecked()
    filled.setChecked(False)
    drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterApply").click()
    app.processEvents()

    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Harmony 1"
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsMoreFilters").click()
    app.processEvents()
    drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterReset").click()
    drawer.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsFilterApply").click()
    app.processEvents()
    assert table.rowCount() == 2
    assert len(store.list_assignments()) == len(before_assignments)
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_classrooms_paginates_and_saves_detail_without_assignment_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    for index in range(12):
        store.seed_assignment(
            school="Hawthorne",
            classroom=f"Room {index + 1:02d}",
            position_name="Teacher 1",
            position_type="Teacher",
            status="filled",
            person_name=f"Teacher {index + 1:02d}",
        )
    before_assignments = store.list_assignments()
    before_history = {assignment.id: store.active_history_count(assignment.id) for assignment in before_assignments}
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsNavButton").click()
    app.processEvents()

    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ClassroomsTable")
    assert table.horizontalHeaderItem(0).text() == "Classroom"
    assert table.rowCount() == 10
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsResultCount").text() == "Showing 1 to 10 of 12 classrooms"
    next_page = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsNextPage")
    previous_page = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsPreviousPage")
    current_page = page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsCurrentPage")
    rows_per_page = page.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsRowsPerPage")
    assert next_page.isEnabled()
    assert not previous_page.isEnabled()
    next_page.click()
    app.processEvents()
    assert current_page.text() == "2"
    assert table.rowCount() == 2
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsResultCount").text() == "Showing 11 to 12 of 12 classrooms"
    previous_page.click()
    app.processEvents()
    rows_per_page.setCurrentText("25 / page")
    app.processEvents()
    assert table.rowCount() == 12
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsResultCount").text() == "Showing 1 to 12 of 12 classrooms"

    name = page.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsDetailNameEdit")
    program = page.findChild(qt_widgets.QComboBox, "StaffingV2ClassroomsDetailProgramEdit")
    capacity = page.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsDetailCapacityEdit")
    display_order = page.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsDetailDisplayOrderEdit")
    assert name.text() == "Room 01"
    name.setText("Room 01A")
    program.setCurrentText("Pre-K")
    capacity.setText("22")
    display_order.setText("4")
    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsSaveButton").click()
    app.processEvents()

    updated = next(classroom for classroom in store.list_classrooms() if classroom.name == "Room 01A")
    assert updated.program == "Pre-K"
    assert updated.licensed_capacity == 22
    assert updated.display_order == 4
    assert len(store.list_assignments()) == len(before_assignments)
    assert {assignment.id: store.active_history_count(assignment.id) for assignment in before_assignments} == before_history
    assert "Room 01A" in _widget_text(page.findChild(qt_widgets.QFrame, "StaffingV2ClassroomsDetailPanel"))

    page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsDeactivateButton").click()
    app.processEvents()
    status = page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsDetailStatus")
    assert "active assignments" in status.text()
    assert any(classroom.id == updated.id for classroom in store.list_classrooms())

    page.findChild(qt_widgets.QLineEdit, "StaffingV2ClassroomsSearch").setText("Room 12")
    app.processEvents()
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ClassroomsCurrentPage").text() == "1"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ClassroomsResultCount").text() == "Showing 1 to 1 of 1 classrooms"

    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_add_position_dialog_creates_need_now_position_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    before_count = len(store.list_assignments())
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()

    page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2AddPositionDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    dialog_text = _widget_text(dialog)
    assert "Add Position" in dialog_text
    assert "Create a new position for a classroom." in dialog_text
    assert "Status Definitions" in dialog_text
    assert "Need Now" in dialog_text
    assert "Don't Need Now" in dialog_text
    assert "Coming" in dialog_text
    assert "Filled" in dialog_text
    close_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionClose")
    assert close_button is not None
    assert close_button.text() == ""
    assert not close_button.icon().isNull()
    assert dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionSchool").currentText() == "Hawthorne"
    assert dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionClassroom").currentText() == "Harmony 1"
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionType").setCurrentText("Teacher")
    name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddPositionName")
    name.setText("Teacher 2")
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionInitialStatus").setCurrentText("Need Now")
    notes = dialog.findChild(qt_widgets.QTextEdit, "StaffingV2AddPositionNotes")
    notes.setPlainText("Added from v2 dialog.")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionSubmit").click()
    app.processEvents()

    assignments = store.list_assignments()
    assert len(assignments) == before_count + 1
    created = next(row for row in assignments if row.position_name == "Teacher 2")
    assert created.status == "need_now"
    assert created.classroom == "Harmony 1"
    assert created.classroom_program == "Preschool"
    assert created.classroom_capacity == 24
    assert created.notes == "Added from v2 dialog."
    assert store.active_history_count(created.id) == 1
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert "Teacher 2" in table_text
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_add_position_submit_immediately_shows_created_position_when_filters_are_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "filled"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    director_model = pyside_interview_app.build_director_staffing_model(full_model, school="Hawthorne")
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    page = window.stack.widget(0)
    page.findChild(qt_widgets.QLineEdit, "StaffingV2Search").setText("not-visible")
    app.processEvents()
    assert page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable").rowCount() == 0

    page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").click()
    app.processEvents()
    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2AddPositionDialog")
    assert dialog is not None
    position_type = dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionType")
    assert "Director" in [position_type.itemText(index) for index in range(position_type.count())]
    position_type.setCurrentText("Director")
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddPositionName").setText("Director")
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionInitialStatus").setCurrentText("Need Now")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionSubmit").click()
    app.processEvents()

    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    visible_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert page.findChild(qt_widgets.QLineEdit, "StaffingV2Search").text() == ""
    assert "Director" in visible_text
    assert page.findChild(qt_widgets.QLabel, "StaffingV2DrawerPositionName").text() == "Director"
    created = next(row for row in window.staffing_store.list_assignments() if row.position_name == "Director")
    assert created.position_type == "Director"
    page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkComing").click()
    app.processEvents()
    coming_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2MarkComingDialog")
    assert coming_dialog is not None
    role = coming_dialog.findChild(qt_widgets.QComboBox, "StaffingV2ComingRole")
    assert "Director" in [role.itemText(index) for index in range(role.count())]
    assert role.currentText() == "Director"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_delete_position_removes_accidental_director(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Office",
                                "program": "Support",
                                "slots": [
                                    {"position_name": "Office", "position_type": "Office", "status": "filled", "person_name": "Violet"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    director_model = pyside_interview_app.build_director_staffing_model(full_model, school="Hawthorne")
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    page = window.stack.widget(0)

    page.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionButton").click()
    app.processEvents()
    add_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2AddPositionDialog")
    add_dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionType").setCurrentText("Director")
    add_dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddPositionName").setText("Director")
    add_dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPositionInitialStatus").setCurrentText("Need Now")
    add_dialog.findChild(qt_widgets.QPushButton, "StaffingV2AddPositionSubmit").click()
    app.processEvents()

    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    director_button = _staffing_button_for_position(table, "Director")
    delete_action = next(action for action in director_button.menu().actions() if action.text() == "Delete Position")
    delete_action.trigger()
    app.processEvents()
    delete_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2DeletePositionDialog")
    assert delete_dialog is not None
    assert "Director" in _widget_text(delete_dialog)
    delete_dialog.findChild(qt_widgets.QPushButton, "StaffingV2DeletePositionConfirm").click()
    app.processEvents()

    assert [row.position_name for row in store.list_assignments()] == ["Office"]
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    refreshed_text = {
        refreshed_table.item(row, column).text()
        for row in range(refreshed_table.rowCount())
        for column in range(refreshed_table.columnCount())
        if refreshed_table.item(row, column) is not None
    }
    assert "Director" not in refreshed_text
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_position_detail_drawer_opens_from_position_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"},
                                    {
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "start_date": "2026-07-08",
                                        "person": {"name": "Imgard", "permit_status": "permit_in_process"},
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    teacher_row = _staffing_row_for_position(table, "Teacher 1")

    table.cellClicked.emit(teacher_row, 0)
    app.processEvents()

    drawer = page.findChild(qt_widgets.QFrame, "StaffingV2PositionDrawer")
    assert drawer is not None
    assert not drawer.isHidden()
    assert page.findChild(qt_widgets.QLabel, "StaffingV2DrawerTitle").text() == "Position Detail"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2DrawerPositionName").text() == "Teacher 1"
    drawer_text = _widget_text(drawer)
    assert "Harmony 1" in drawer_text
    assert "Hawthorne" in drawer_text
    assert "Need Now" in drawer_text
    assert "OPEN POSITION" in drawer_text
    assert "Position Overview" in drawer_text
    assert "Available Next Actions" in drawer_text
    assert "Data Integrity / Validation" in drawer_text
    assert "Lifecycle History" in drawer_text
    assert "Related Person" in drawer_text
    assert "No person is currently assigned to this position." in drawer_text
    assign_person = drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerAssignPerson")
    assert assign_person is not None
    assert assign_person.text() == "Assign or Create Person"
    assert not assign_person.isEnabled()
    assert not assign_person.icon().isNull()
    assert _icon_has_primary_blue(assign_person.icon())
    assert drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkComing").text() == "Mark Coming"
    assert drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkDontNeed").text() == "Mark Not Needed"
    history_button = drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerViewHistory")
    assert history_button.text() == "View Full History"
    assert history_button.isEnabled()
    assert not drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkComing").icon().isNull()
    assert not drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkDontNeed").icon().isNull()
    assert not history_button.icon().isNull()
    close = page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerClose")
    assert close is not None
    assert close.text() == ""
    assert not close.icon().isNull()
    footer_buttons = {
        button.objectName(): button.text()
        for button in drawer.findChildren(qt_widgets.QPushButton)
        if button.objectName().startswith("StaffingV2Drawer")
    }
    assert footer_buttons["StaffingV2DrawerCancel"] == "Cancel"
    assert footer_buttons["StaffingV2DrawerSaveDraft"] == "Save Draft"
    assert footer_buttons["StaffingV2DrawerSaveChanges"] == "Save Changes"
    assert not drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerCancel").icon().isNull()
    assert not drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerSaveDraft").icon().isNull()
    assert not drawer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerSaveChanges").icon().isNull()
    history_button.click()
    app.processEvents()
    assert page.findChild(qt_widgets.QWidget, "StaffingV2AssignmentHistoryDashboard") is not None
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryNavButton").property("staffingV2ActiveNav") is True
    assert len(store.list_assignments()) == 2
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_position_drawer_wires_open_and_edit_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        status="dont_need_now",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    table.cellClicked.emit(_staffing_row_for_position(table, "Teacher 1"), 0)
    app.processEvents()

    page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkNeedNow").click()
    app.processEvents()

    assert store.get_assignment(assignment_id).status == "need_now"
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_button_for_position(table, "Teacher 1").text() == "Mark Coming"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkComing") is not None

    page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerEditPosition").click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2EditPositionDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2EditPositionName")
    assert name.text() == "Teacher 1"
    name.setText("Teacher 1A")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2EditPositionSubmit").click()
    app.processEvents()

    assert store.get_assignment(assignment_id).position_name == "Teacher 1A"
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_row_for_position(refreshed_table, "Teacher 1A") == 0
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_mark_coming_dialog_saves_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "program": "Preschool",
                                "licensed_capacity": 24,
                                "slots": [
                                    {
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "need_now",
                                        "current_opened_date": "2026-07-01",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    store.import_seed_file(seed_path)
    assignment_id = next(row.id for row in store.list_assignments() if row.position_name == "Teacher 1")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")

    _staffing_button_for_position(table, "Teacher 1").click()
    app.processEvents()

    assert window.window.findChild(qt_widgets.QDialog, "PySideStaffingMarkComingDialog") is None
    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2MarkComingDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    dialog_text = _widget_text(dialog)
    assert "Mark Coming" in dialog_text
    assert "Position Summary" in dialog_text
    assert "Candidate Selection" in dialog_text
    assert "Candidate Details" in dialog_text
    assert "Validation / Requirements" in dialog_text
    assert "What will happen on save" in dialog_text
    assert "This action does not close the open assignment history cycle." in dialog_text
    close_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingClose")
    assert close_button is not None
    assert close_button.text() == ""
    assert not close_button.icon().isNull()
    assert not dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingSelectPerson").icon().isNull()
    assert not dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingCreatePerson").icon().isNull()
    assert _icon_has_primary_blue(dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingCreatePerson").icon())
    assert not dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingSubmit").icon().isNull()
    people_search = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2ComingPeopleSearch")
    people_search.setText("Emily")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingSelectPerson").click()
    app.processEvents()
    assert people_search.selectedText() == "Emily"
    full_name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2ComingFullName")
    full_name.setText("Temporary Name")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingCreatePerson").click()
    app.processEvents()
    assert full_name.text() == ""

    full_name.setText("Emily Carter")
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2ComingRole").setCurrentText("Teacher")
    dialog.findChild(qt_widgets.QDateEdit, "StaffingV2ComingStartDate").setDate(qt_core.QDate(2026, 8, 1))
    dialog.findChild(qt_widgets.QComboBox, "StaffingV2ComingPermitStatus").setCurrentText("Permit in Process")
    dialog.findChild(qt_widgets.QSpinBox, "StaffingV2ComingUnits").setValue(12)
    assert dialog.findChild(qt_widgets.QCheckBox, "StaffingV2ComingActive").isChecked()
    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2ComingNotes").setPlainText("Hiring notes stay UI-only for this slice.")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2ComingSubmit").click()
    app.processEvents()

    updated = store.get_assignment(assignment_id)
    assert updated.status == "coming"
    assert updated.person_name == "Emily Carter"
    assert updated.start_date == "2026-08-01"
    assert updated.permit_status == "permit_in_process"
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_button_for_position(refreshed_table, "Teacher 1").text() == "Mark Filled"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_mark_filled_dialog_uses_coming_start_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    service = pyside_interview_app.StaffingService(
        store,
        clock=lambda: "2026-07-01T09:00:00Z",
    )
    service.open_position(assignment_id)
    service.mark_coming(assignment_id, person_name="Emily Carter", start_date="2026-07-08")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")

    _staffing_button_for_position(table, "Teacher 1").click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2MarkFilledDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    dialog_text = _widget_text(dialog)
    assert "Mark Filled" in dialog_text
    assert "Position Summary" in dialog_text
    assert "Assigned Person" in dialog_text
    assert "Start Confirmation" in dialog_text
    assert "Validation / Requirements" in dialog_text
    assert "What will happen on save" in dialog_text
    assert "This action closes the current open assignment history cycle." in dialog_text
    assert window.window.findChild(qt_widgets.QDialog, "PySideStaffingMarkFilledDialog") is None
    close_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2FilledClose")
    assert close_button is not None
    assert close_button.text() == ""
    assert not close_button.icon().isNull()
    filled_date = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2FilledDate")
    assert filled_date is not None
    assert filled_date.text() == "2026-07-08"
    assert not filled_date.isEnabled()
    assert dialog.findChild(qt_widgets.QDateEdit, "StaffingV2FilledDate") is None

    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2FilledNotes").setPlainText("Started and verified.")
    assert dialog.findChild(qt_widgets.QCheckBox, "StaffingV2FilledStarted").isChecked()
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2FilledSubmit").click()
    app.processEvents()

    updated = store.get_assignment(assignment_id)
    assert updated.status == "filled"
    assert updated.person_name == "Emily Carter"
    assert updated.current_filled_date == "2026-07-08"
    assert store.closed_days_to_fill() == [7]
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_button_for_position(refreshed_table, "Teacher 1").text() == "Manage Filled"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_manage_filled_dialog_selects_next_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_test = pytest.importorskip("PySide6.QtTest")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 2",
        position_type="Teacher",
        status="filled",
        person_name="Imgard",
        permit_status="permit_in_process",
    )
    before = store.get_assignment(assignment_id)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    button = _staffing_button_for_position(table, "Teacher 2")

    assert button.text() == "Manage Filled"
    assert button.isEnabled()
    button.click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ManageFilledDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    dialog_text = _widget_text(dialog)
    assert "Manage Filled Position" in dialog_text
    assert "Choose what you want to do with this filled position." in dialog_text
    assert "Imgard" in dialog_text
    assert "Teacher 2" in dialog_text
    assert "Filled" in dialog_text
    assert "Permit in Process" in dialog_text
    assert "Update Permit Status" in dialog_text
    assert "Replace Employee" in dialog_text
    assert "What happens next" in dialog_text
    assert "This step does not change anything until you continue" in dialog_text
    close_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2ManageFilledClose")
    assert close_button is not None
    assert close_button.text() == ""
    assert not close_button.icon().isNull()

    permit_option = dialog.findChild(qt_widgets.QRadioButton, "StaffingV2ManageFilledPermitOption")
    replace_option = dialog.findChild(qt_widgets.QRadioButton, "StaffingV2ManageFilledReplaceOption")
    continue_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2ManageFilledContinue")
    assert permit_option.isChecked()
    assert not replace_option.isChecked()
    replace_option.click()
    app.processEvents()
    assert replace_option.isChecked()
    assert continue_button.text() == "Continue"

    continue_button.click()
    app.processEvents()
    replace_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ReplaceEmployeeDialog")
    assert replace_dialog is not None
    assert not replace_dialog.isHidden()
    assert window.window.findChild(qt_widgets.QDialog, "PySideStaffingReplaceDialog") is None
    replace_text = _widget_text(replace_dialog)
    assert "Replace Employee" in replace_text
    assert "Position Summary" in replace_text
    assert "Replacement Details" in replace_text
    assert "Validation / Requirements" in replace_text
    assert "What will happen on save" in replace_text
    close = replace_dialog.findChild(qt_widgets.QPushButton, "StaffingV2ReplaceClose")
    assert close is not None
    assert close.text() == ""
    assert not close.icon().isNull()
    notice = replace_dialog.findChild(qt_widgets.QDateEdit, "StaffingV2ReplaceNotice")
    final_day = replace_dialog.findChild(qt_widgets.QDateEdit, "StaffingV2ReplaceFinalDay")
    reason = replace_dialog.findChild(qt_widgets.QComboBox, "StaffingV2ReplaceReason")
    assert notice is not None
    assert final_day is not None
    assert notice.calendarPopup()
    assert final_day.calendarPopup()
    assert notice.date() == qt_core.QDate.currentDate()
    assert final_day.date() == qt_core.QDate.currentDate()
    assert reason is not None
    assert [reason.itemText(index) for index in range(reason.count())] == [
        "Resignation",
        "Termination",
        "Leave of absence",
        "Transfer",
        "Other",
    ]
    submit = replace_dialog.findChild(qt_widgets.QPushButton, "StaffingV2ReplaceSubmit")
    qt_test.QTest.mouseClick(notice, qt_core.Qt.MouseButton.LeftButton, pos=notice.rect().center())
    app.processEvents()
    assert notice.calendarWidget().isVisible()
    after = store.get_assignment(assignment_id)
    assert after.status == before.status
    assert after.person_name == before.person_name
    assert after.permit_status == before.permit_status

    notice.setDate(qt_core.QDate(2026, 8, 1))
    final_day.setDate(qt_core.QDate(2026, 8, 15))
    reason.setCurrentText("Transfer")
    submit.click()
    app.processEvents()
    replaced = store.get_assignment(assignment_id)
    assert replaced.status == "replace"
    assert replaced.person_name == before.person_name
    assert replaced.notice_given == "2026-08-01"
    assert replaced.final_working_day == "2026-08-15"
    page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerMarkNeedNow").click()
    app.processEvents()
    need_now_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2MarkNeedNowDialog")
    assert need_now_dialog is not None
    need_now_submit = need_now_dialog.findChild(qt_widgets.QPushButton, "StaffingV2NeedNowSubmit")
    need_now_submit.click()
    need_now_submit.click()
    app.processEvents()
    reopened = store.get_assignment(assignment_id)
    assert reopened.status == "need_now"
    assert reopened.person_id is None
    assert reopened.person_name == ""
    assert reopened.start_date == ""
    assert need_now_dialog.isHidden()
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_button_for_position(refreshed_table, "Teacher 2").text() == "Mark Coming"
    drawer = page.findChild(qt_widgets.QFrame, "StaffingV2PositionDrawer")
    assert drawer is not None
    assert "Need Now" in _widget_text(drawer)
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_replace_today_reopens_need_now_and_clears_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 2",
        position_type="Teacher",
        status="filled",
        person_name="Imgard",
        permit_status="permit_in_process",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")

    _staffing_button_for_position(table, "Teacher 2").click()
    app.processEvents()
    manage_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ManageFilledDialog")
    manage_dialog.findChild(qt_widgets.QRadioButton, "StaffingV2ManageFilledReplaceOption").click()
    manage_dialog.findChild(qt_widgets.QPushButton, "StaffingV2ManageFilledContinue").click()
    app.processEvents()
    replace_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ReplaceEmployeeDialog")
    final_day = replace_dialog.findChild(qt_widgets.QDateEdit, "StaffingV2ReplaceFinalDay")
    assert final_day.date() == qt_core.QDate.currentDate()

    replace_dialog.findChild(qt_widgets.QPushButton, "StaffingV2ReplaceSubmit").click()
    app.processEvents()

    updated = store.get_assignment(assignment_id)
    assert updated.status == "need_now"
    assert updated.person_id is None
    assert updated.person_name == ""
    assert updated.start_date == ""
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_button_for_position(refreshed_table, "Teacher 2").text() == "Mark Coming"
    drawer = page.findChild(qt_widgets.QFrame, "StaffingV2PositionDrawer")
    assert drawer is not None
    drawer_text = _widget_text(drawer)
    assert "Need Now" in drawer_text
    assert "OPEN POSITION" in drawer_text
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_update_permit_dialog_saves_people_permit_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 2",
        position_type="Teacher",
        status="filled",
        person_name="Imgard",
        permit_status="permit_in_process",
    )
    person_id = store.get_assignment(assignment_id).person_id or 0
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    _staffing_button_for_position(table, "Teacher 2").click()
    app.processEvents()
    manage = window.window.findChild(qt_widgets.QDialog, "StaffingV2ManageFilledDialog")
    manage.findChild(qt_widgets.QPushButton, "StaffingV2ManageFilledContinue").click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2UpdatePermitDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    dialog_text = _widget_text(dialog)
    assert "Update Permit Status" in dialog_text
    assert "Update the employee permit level without reopening this position." in dialog_text
    assert "Position Summary" in dialog_text
    assert "Permit Update" in dialog_text
    assert "Validation / Requirements" in dialog_text
    assert "What will happen on save" in dialog_text
    assert "This action updates People only and does not reopen the staffing cycle." in dialog_text
    close_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2PermitClose")
    assert close_button is not None
    assert close_button.text() == ""
    assert not close_button.icon().isNull()
    assert dialog.findChild(qt_widgets.QLineEdit, "StaffingV2PermitEmployeeName").text() == "Imgard"
    assert not dialog.findChild(qt_widgets.QLineEdit, "StaffingV2PermitEmployeeName").isEnabled()
    assert dialog.findChild(qt_widgets.QLineEdit, "StaffingV2PermitRole").text() == "Teacher"
    assert not dialog.findChild(qt_widgets.QLineEdit, "StaffingV2PermitRole").isEnabled()

    dialog.findChild(qt_widgets.QComboBox, "StaffingV2PermitNewStatus").setCurrentText("Teacher Permit")
    dialog.findChild(qt_widgets.QDateEdit, "StaffingV2PermitEffectiveDate").setDate(qt_core.QDate(2026, 7, 6))
    dialog.findChild(qt_widgets.QSpinBox, "StaffingV2PermitUnits").setValue(24)
    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2PermitNotes").setPlainText("Permit file received.")
    assert dialog.findChild(qt_widgets.QCheckBox, "StaffingV2PermitDocumentationReceived").isChecked()
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2PermitSubmit").click()
    app.processEvents()

    updated = store.get_assignment(assignment_id)
    with store.connect() as conn:
        person = store.person_context(conn, person_id)
    assert updated.status == "filled"
    assert updated.permit_status == "teacher_permit_approved"
    assert person.permit_effective_date == "2026-07-06"
    assert person.units == 24
    assert person.permit_documentation_received is True
    assert person.permit_notes == "Permit file received."
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_mark_need_now_dialog_clears_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 2",
        position_type="Teacher",
        status="filled",
        person_name="Maria Gonzalez",
        permit_status="teacher_permit_approved",
    )
    service = pyside_interview_app.StaffingService(store, clock=lambda: "2026-05-01T09:00:00Z")
    service.mark_replacing(assignment_id, notice_given="2026-05-01", final_working_day="2026-05-22")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    button = _staffing_button_for_position(table, "Teacher 2")

    assert button.text() == "Mark Need Now"
    button.click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2MarkNeedNowDialog")
    assert dialog is not None
    assert not dialog.isHidden()
    dialog_text = _widget_text(dialog)
    assert "Mark Position as Need Now" in dialog_text
    assert "This action will change the status from Replace to Need Now and reopen the position for hiring." in dialog_text
    assert "Position Summary" in dialog_text
    assert "Harmony 1 (Hawthorne)" in dialog_text
    assert "Maria Gonzalez" in dialog_text
    assert "May 1, 2026" in dialog_text
    assert "May 22, 2026" in dialog_text
    assert "What will happen" in dialog_text
    assert "Status will change from Replace to Need Now" in dialog_text
    assert "Teacher name and start date will be cleared" in dialog_text
    assert "Options" in dialog_text
    close_button = dialog.findChild(qt_widgets.QPushButton, "StaffingV2NeedNowClose")
    assert close_button is not None
    assert close_button.text() == ""
    assert not close_button.icon().isNull()
    checkbox = dialog.findChild(qt_widgets.QCheckBox, "StaffingV2NeedNowClearPerson")
    assert checkbox is not None
    assert checkbox.isChecked()
    assert "This will remove Maria Gonzalez as the assigned person for this position." in dialog_text

    dialog.findChild(qt_widgets.QPushButton, "StaffingV2NeedNowSubmit").click()
    app.processEvents()

    updated = store.get_assignment(assignment_id)
    assert updated.status == "need_now"
    assert updated.person_id is None
    assert updated.person_name == ""
    assert updated.start_date == ""
    assert store.active_history_count(assignment_id) == 1
    refreshed_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    assert _staffing_button_for_position(refreshed_table, "Teacher 2").text() == "Mark Coming"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_people_dashboard_renders_employee_management_from_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    maria_assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        status="filled",
        person_name="Maria Gonzalez",
        permit_status="teacher_permit_approved",
    )
    sofia_assignment_id = store.seed_assignment(
        school="North Long Beach",
        classroom="Unity 1",
        position_name="Aide 1",
        position_type="Aide",
        status="filled",
        person_name="Sofia Ramirez",
        permit_status="permit_in_process",
    )
    with store.connect() as conn:
        maria_person_id = store.get_assignment(maria_assignment_id).person_id
        sofia_person_id = store.get_assignment(sofia_assignment_id).person_id
        conn.execute("UPDATE people SET units = 18 WHERE id = ?", (maria_person_id,))
        conn.execute("UPDATE people SET units = 6 WHERE id = ?", (sofia_person_id,))
    before_people = store.list_people()
    before_assignments = store.list_assignments()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "North Long Beach"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()

    page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleNavButton").click()
    app.processEvents()

    assert page.findChild(qt_widgets.QWidget, "StaffingV2PeopleDashboard") is not None
    assert page.findChild(qt_widgets.QLabel, "StaffingV2PeopleTitle").text() == "People / Employee Management"
    assert (
        page.findChild(qt_widgets.QLabel, "StaffingV2PeopleSubtitle").text()
        == "Manage employee records, permits, roles, and assignments."
    )
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleAddButton").text() == "Add Person"
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleAddButton").icon().isNull()
    people_search = page.findChild(qt_widgets.QLineEdit, "StaffingV2PeopleSearch")
    assert people_search.placeholderText() == "Search by name, role, or email..."
    assert people_search.actions()
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2PeopleActiveFilter").currentText() == "All"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2PeopleRoleFilter").currentText() == "All"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2PeoplePermitFilter").currentText() == "All"
    people_more_filters = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleMoreFilters")
    assert people_more_filters.text() == "More Filters"
    assert not people_more_filters.icon().isNull()
    assert people_more_filters.isEnabled()
    people_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PeopleTable")
    people_table_width = people_table.width()
    people_more_filters.click()
    app.processEvents()
    people_filter_drawer = page.findChild(qt_widgets.QFrame, "StaffingV2PeopleFilterDrawer")
    assert people_filter_drawer is not None
    assert not people_filter_drawer.isHidden()
    assert people_filter_drawer.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2PeopleDashboard")
    assert people_table.width() == people_table_width
    people_filter_scroll = people_filter_drawer.findChild(qt_widgets.QScrollArea, "StaffingV2PeopleFilterDrawerScroll")
    assert people_filter_scroll is not None
    assert people_filter_scroll.widget().findChild(qt_widgets.QPushButton, "StaffingV2PeopleFilterApply") is None
    people_filter_footer = people_filter_drawer.findChild(qt_widgets.QWidget, "StaffingV2PeopleFilterDrawerFooter")
    assert people_filter_footer is not None
    assert people_filter_footer.findChild(qt_widgets.QPushButton, "StaffingV2PeopleFilterApply") is not None
    people_filter_text = _widget_text(people_filter_drawer)
    assert "Filters" in people_filter_text
    assert "Active Status" in people_filter_text
    assert "Role" in people_filter_text
    assert "Permit Status" in people_filter_text
    people_drawer_role = people_filter_drawer.findChild(qt_widgets.QComboBox, "StaffingV2PeopleFilterRole")
    assert people_drawer_role.currentText() == "All"
    people_drawer_role.setCurrentText("Teacher")
    people_filter_drawer.findChild(qt_widgets.QPushButton, "StaffingV2PeopleFilterApply").click()
    app.processEvents()
    filtered_table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PeopleTable")
    assert filtered_table.rowCount() == 1
    assert filtered_table.item(0, 0).text() == "Maria Gonzalez"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2PeopleResultCount").text() == "Showing 1 to 1 of 1 people"
    page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleClear").click()
    app.processEvents()
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleClear").text() == "Clear"
    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2PeopleMetricCard"))
    assert "Total People 2" in metric_text
    assert "Active 2" in metric_text
    assert "Teachers 1" in metric_text
    assert "Aides 1" in metric_text
    assert "Avg Units 12.0" in metric_text
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PeopleTable")
    assert table.rowCount() == 2
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert {
        "Maria Gonzalez",
        "Teacher",
        "Teacher Permit",
        "18",
        "Active",
        "Hawthorne\nHarmony 1 - Teacher 1",
        "Sofia Ramirez",
        "Aide",
        "Permit in Process",
    } <= table_text
    for row in range(table.rowCount()):
        view_button = table.cellWidget(row, 6)
        assert isinstance(view_button, qt_widgets.QPushButton)
        assert view_button.text() == "View"
        assert not view_button.icon().isNull()
        assert view_button.isEnabled()
    sofia_row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == "Sofia Ramirez")
    sofia_view = table.cellWidget(sofia_row, 6)
    assert isinstance(sofia_view, qt_widgets.QPushButton)
    sofia_view.click()
    app.processEvents()
    assert page.findChild(qt_widgets.QLabel, "StaffingV2PeopleResultCount").text() == "Showing 1 to 2 of 2 people"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2PeopleRowsPerPage").currentText() == "10 / page"
    detail = page.findChild(qt_widgets.QFrame, "StaffingV2PeopleDetailPanel")
    assert detail.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2PeopleDashboard")
    people_detail_scroll = detail.findChild(qt_widgets.QScrollArea, "StaffingV2PeopleDetailPanelScroll")
    assert people_detail_scroll is not None
    assert people_detail_scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    people_tabs = page.findChild(qt_widgets.QFrame, "StaffingV2PeopleDetailTabs")
    assert people_tabs is not None
    assert people_tabs.y() < 160
    assert people_tabs.width() <= people_detail_scroll.viewport().width()
    assert people_detail_scroll.widget().findChild(qt_widgets.QPushButton, "StaffingV2PeopleEditButton") is None
    people_detail_close = detail.findChild(qt_widgets.QPushButton, "StaffingV2PeopleDetailClose")
    assert people_detail_close is not None
    assert people_detail_close.text() == ""
    assert not people_detail_close.icon().isNull()
    people_detail_footer = detail.findChild(qt_widgets.QWidget, "StaffingV2PeopleDetailPanelFooter")
    assert people_detail_footer is not None
    assert people_detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2PeopleDetailClose") is None
    detail_text = _widget_text(detail)
    assert "Sofia Ramirez" in detail_text
    assert "SR" in detail_text
    assert "Employee Information" in detail_text
    assert "Role Aide" in detail_text
    assert "Permit Status Permit in Process" in detail_text
    assert "Units 6" in detail_text
    assert "Current Assignment" in detail_text
    assert "North Long Beach" in detail_text
    assert "Unity 1 - Aide 1" in detail_text
    assert "Employment Status" in detail_text
    assert "Additional Information" in detail_text
    for detail_card in detail.findChildren(qt_widgets.QFrame, "StaffingV2PeopleDetailCard"):
        assert detail_card.sizePolicy().verticalPolicy() == qt_widgets.QSizePolicy.Policy.Maximum
    overview_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleOverviewTab")
    assignments_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleAssignmentsTab")
    history_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleHistoryTab")
    notes_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleNotesTab")
    documents_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleDocumentsTab")
    assert [tab.text() for tab in [overview_tab, assignments_tab, history_tab, notes_tab, documents_tab]] == [
        "Overview",
        "Assignments",
        "History",
        "Notes",
        "Documents",
    ]
    assert overview_tab.property("staffingV2ActivePeopleTab") is True
    for inactive_tab in [assignments_tab, history_tab, notes_tab, documents_tab]:
        assert inactive_tab.property("staffingV2ActivePeopleTab") is False
    history_tab.click()
    app.processEvents()
    assert history_tab.property("staffingV2ActivePeopleTab") is True
    assert overview_tab.property("staffingV2ActivePeopleTab") is False
    deactivate_button = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleDeactivateButton")
    assert deactivate_button.text() == "Deactivate Employee"
    assert not deactivate_button.icon().isNull()
    edit_button = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleEditButton")
    assert edit_button.text() == "Edit Person"
    assert not edit_button.icon().isNull()
    assert people_detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2PeopleEditButton") is edit_button
    assert len(store.list_people()) == len(before_people)
    assert len(store.list_assignments()) == len(before_assignments)
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_staffing_v2_add_person_dialog_creates_person_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    before_people = store.list_people()
    before_assignments = store.list_assignments()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleNavButton").click()
    app.processEvents()

    add_person = page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleAddButton")
    assert add_person.isEnabled()
    add_person.click()
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2AddPersonDialog")
    assert dialog is not None
    assert "Add Person" in _widget_text(dialog)
    name = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddPersonName")
    role = dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPersonRole")
    permit = dialog.findChild(qt_widgets.QComboBox, "StaffingV2AddPersonPermit")
    units = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2AddPersonUnits")
    name.setText("Nina Patel")
    role.setCurrentText("Aide")
    permit.setCurrentText("Permit in Process")
    units.setText("5")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2AddPersonSave").click()
    app.processEvents()

    people = store.list_people()
    assert len(people) == len(before_people) + 1
    created = people[0]
    assert created.name == "Nina Patel"
    assert created.role == "Aide"
    assert created.permit_status == "permit_in_process"
    assert created.units == 5
    assert len(store.list_assignments()) == len(before_assignments)
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PeopleTable")
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Nina Patel"
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_staffing_v2_assignment_history_dashboard_renders_history_from_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    closed_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    open_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Quest",
        position_name="Teacher 2",
        position_type="Teacher",
        status="need_now",
    )
    with store.connect() as conn:
        person_id = store.ensure_person(conn, "Emily Carter", "Teacher", "permit_in_process", "2026-07-05T09:00:00Z")
        closed_classroom_id = conn.execute("SELECT classroom_id FROM assignments WHERE id = ?", (closed_id,)).fetchone()["classroom_id"]
        conn.execute(
            """
            UPDATE assignments
            SET person_id = ?, status = 'filled', current_opened_date = '2026-05-08',
                current_filled_date = '2026-05-20'
            WHERE id = ?
            """,
            (person_id, closed_id),
        )
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, filled_date,
                days_to_fill, closed_reason, created_at, updated_at
            ) VALUES (?, ?, 'Teacher 1', '2026-05-08', '2026-05-20', 12, 'filled',
                '2026-05-08T09:15:00Z', '2026-05-20T14:05:00Z')
            """,
            (closed_id, closed_classroom_id),
        )
        open_classroom_id = conn.execute("SELECT classroom_id FROM assignments WHERE id = ?", (open_id,)).fetchone()["classroom_id"]
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
            ) VALUES (?, ?, 'Teacher 2', '2026-07-01', '2026-07-01T09:15:00Z', '2026-07-01T09:15:00Z')
            """,
            (open_id, open_classroom_id),
        )
    before_records = store.list_assignment_history()
    before_assignments = store.list_assignments()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()

    page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryNavButton").click()
    app.processEvents()

    assert page.findChild(qt_widgets.QWidget, "StaffingV2AssignmentHistoryDashboard") is not None
    assert page.findChild(qt_widgets.QLabel, "StaffingV2HistoryTitle").text() == "Assignment History"
    assert "Review open-to-fill staffing cycles" in page.findChild(qt_widgets.QLabel, "StaffingV2HistorySubtitle").text()
    history_export_button = page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryExportButton")
    assert history_export_button.text() == "Export"
    assert not history_export_button.icon().isNull()
    assert history_export_button.isEnabled()
    history_export_button.click()
    app.processEvents()
    history_export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2HistoryExportDialog")
    assert history_export_dialog is not None
    history_export_text = _widget_text(history_export_dialog)
    assert "Export Assignment History" in history_export_text
    assert "Total records 2" in history_export_text
    assert f"A-{closed_id:04d}" in history_export_text
    assert f"A-{open_id:04d}" in history_export_text
    history_export_dialog.accept()
    app.processEvents()
    history_validation_button = page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryValidationButton")
    assert history_validation_button.text() == "View Validation"
    assert history_validation_button.isEnabled()
    assert not history_validation_button.icon().isNull()
    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2HistoryMetricCard"))
    assert "Total Cycles 2" in metric_text
    assert "Open Cycles 1" in metric_text
    assert "Closed Cycles 1" in metric_text
    assert "Avg Days to Fill 12.0" in metric_text
    assert "Data Issues 1" in metric_text
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2HistorySchoolFilter").currentText() == "All Schools"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2HistoryClassroomFilter").currentText() == "All Classrooms"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2HistoryCycleFilter").currentText() == "All Statuses"
    history_date_range = page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryDateRangeFilter")
    assert history_date_range.text() == "2026-05-08 - 2026-07-01"
    assert not history_date_range.icon().isNull()
    assert not history_date_range.isEnabled()
    history_search = page.findChild(qt_widgets.QLineEdit, "StaffingV2HistorySearch")
    assert history_search.placeholderText() == "Search assignments..."
    assert history_search.actions()
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryMoreFilters").icon().isNull()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2HistoryTable")
    assert table.rowCount() == 2
    assert table.columnCount() == 10
    assert table.horizontalHeaderItem(9).text() == "Actions"
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert {
        f"A-{open_id:04d}",
        "Quest",
        "Teacher 2",
        "2026-07-01",
        "Open",
        "OPEN POSITION",
        "Warning",
        f"A-{closed_id:04d}",
        "Harmony 1",
        "Teacher 1",
        "2026-05-08",
        "2026-05-20",
        "12",
        "Closed",
        "Emily Carter",
        "Healthy",
    } <= table_text
    for row in range(table.rowCount()):
        view_button = table.cellWidget(row, 9)
        assert isinstance(view_button, qt_widgets.QPushButton)
        assert view_button.text() == "View"
        assert not view_button.icon().isNull()
        assert view_button.isEnabled()
    closed_history_row = next(
        row for row in range(table.rowCount()) if table.item(row, 0).text() == f"A-{closed_id:04d}"
    )
    closed_view = table.cellWidget(closed_history_row, 9)
    assert isinstance(closed_view, qt_widgets.QPushButton)
    closed_view.click()
    app.processEvents()
    closed_detail_panel = page.findChild(qt_widgets.QFrame, "StaffingV2HistoryDetailPanel")
    assert closed_detail_panel.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2AssignmentHistoryDashboard")
    history_detail_scroll = closed_detail_panel.findChild(qt_widgets.QScrollArea, "StaffingV2HistoryDetailPanelScroll")
    assert history_detail_scroll is not None
    assert history_detail_scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    first_history_card = closed_detail_panel.findChild(qt_widgets.QFrame, "StaffingV2HistoryDetailCard")
    assert first_history_card.y() < 160
    assert first_history_card.width() <= history_detail_scroll.viewport().width()
    assert history_detail_scroll.widget().findChild(qt_widgets.QPushButton, "StaffingV2HistoryExportRecord") is None
    history_detail_close = closed_detail_panel.findChild(qt_widgets.QPushButton, "StaffingV2HistoryDetailClose")
    assert history_detail_close is not None
    assert history_detail_close.text() == ""
    assert not history_detail_close.icon().isNull()
    history_detail_footer = closed_detail_panel.findChild(qt_widgets.QWidget, "StaffingV2HistoryDetailPanelFooter")
    assert history_detail_footer is not None
    assert history_detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2HistoryDetailClose") is None
    closed_detail = _widget_text(closed_detail_panel)
    assert f"Assignment ID: A-{closed_id:04d}" in closed_detail
    closed_chip = closed_detail_panel.findChild(qt_widgets.QFrame, "StaffingV2HistoryAssignmentIdChip")
    assert closed_chip is not None
    assert _widget_text(closed_chip) == f"A-{closed_id:04d}"
    for detail_card in closed_detail_panel.findChildren(qt_widgets.QFrame, "StaffingV2HistoryDetailCard"):
        assert detail_card.sizePolicy().verticalPolicy() == qt_widgets.QSizePolicy.Policy.Maximum
    open_employee = closed_detail_panel.findChild(qt_widgets.QPushButton, "StaffingV2HistoryOpenEmployee")
    assert open_employee is not None
    assert open_employee.isEnabled()
    export_record = closed_detail_panel.findChild(qt_widgets.QPushButton, "StaffingV2HistoryExportRecord")
    assert export_record is not None
    assert export_record.isEnabled()
    assert history_detail_footer.findChild(qt_widgets.QPushButton, "StaffingV2HistoryExportRecord") is export_record
    export_record.click()
    app.processEvents()
    export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2HistoryExportRecordDialog")
    assert export_dialog is not None
    export_text = _widget_text(export_dialog)
    assert "Export Record" in export_text
    assert f"A-{closed_id:04d}" in export_text
    assert "Harmony 1" in export_text
    assert "Emily Carter" in export_text
    export_dialog.accept()
    app.processEvents()
    open_employee.click()
    app.processEvents()
    assert page.findChild(qt_widgets.QWidget, "StaffingV2PeopleDashboard") is not None
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2PeopleNavButton").property("staffingV2ActiveNav") is True
    people_detail = page.findChild(qt_widgets.QFrame, "StaffingV2PeopleDetailPanel")
    people_detail_text = _widget_text(people_detail)
    assert "Emily Carter" in people_detail_text
    assert "Teacher" in people_detail_text
    page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryNavButton").click()
    app.processEvents()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2HistoryTable")
    open_history_row = next(
        row for row in range(table.rowCount()) if table.item(row, 0).text() == f"A-{open_id:04d}"
    )
    open_view = table.cellWidget(open_history_row, 9)
    assert isinstance(open_view, qt_widgets.QPushButton)
    open_view.click()
    app.processEvents()
    assert page.findChild(qt_widgets.QLabel, "StaffingV2HistoryResultCount").text() == "Showing 1 to 2 of 2 records"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2HistoryRowsPerPage").currentText() == "10 / page"
    detail = page.findChild(qt_widgets.QFrame, "StaffingV2HistoryDetailPanel")
    detail_text = _widget_text(detail)
    assert f"Assignment ID: A-{open_id:04d}" in detail_text
    assignment_id_chip = detail.findChild(qt_widgets.QFrame, "StaffingV2HistoryAssignmentIdChip")
    assert assignment_id_chip is not None
    assert _widget_text(assignment_id_chip) == f"A-{open_id:04d}"
    assert "Quest" in detail_text
    assert "Lifecycle Events" in detail_text
    assert "Position opened" in detail_text
    lifecycle_rows = page.findChildren(qt_widgets.QFrame, "StaffingV2HistoryLifecycleEventRow")
    assert lifecycle_rows
    assert any(row.property("staffingV2LifecycleEventType") == "opened" for row in lifecycle_rows)
    assert "Validation / Integrity" in detail_text
    assert "Duplicate active cycle" in detail_text
    validation_rows = page.findChildren(qt_widgets.QFrame, "StaffingV2HistoryValidationCheckRow")
    assert len(validation_rows) >= 3
    validation_statuses = {row.property("staffingV2ValidationCheckStatus") for row in validation_rows}
    assert {"pass", "warning"} <= validation_statuses
    view_assignment = page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryViewAssignment")
    assert view_assignment.text() == "View Assignment"
    assert view_assignment.isEnabled()
    assert not view_assignment.icon().isNull()
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryOpenEmployee").icon().isNull()
    assert not page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryExportRecord").icon().isNull()
    view_assignment.click()
    app.processEvents()
    assert page.findChild(qt_widgets.QWidget, "StaffingV2Dashboard") is not None
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2DashboardNavButton").property("staffingV2ActiveNav") is True
    assert page.findChild(qt_widgets.QLabel, "StaffingV2DrawerPositionName").text() == "Teacher 2"
    page.findChild(qt_widgets.QPushButton, "StaffingV2DrawerViewHistory").click()
    app.processEvents()
    history_validation_button = page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryValidationButton")
    history_validation_button.click()
    app.processEvents()
    assert page.findChild(qt_widgets.QWidget, "StaffingV2ValidationDashboard") is not None
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationNavButton").property("staffingV2ActiveNav") is True
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2HistoryNavButton").property("staffingV2ActiveNav") is False
    assert len(store.list_assignment_history()) == len(before_records)
    assert len(store.list_assignments()) == len(before_assignments)
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_staffing_v2_validation_dashboard_and_filter_drawer_use_existing_staffing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    db_path = tmp_path / "staffing.sqlite3"
    store = pyside_interview_app.StaffingStore(db_path)
    store.initialize()
    old_open_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        status="need_now",
        permit_status="unknown",
    )
    coming_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 2",
        position_type="Teacher",
        status="coming",
        person_name="James Mitchell",
        permit_status="permit_in_process",
    )
    store.seed_assignment(
        school="Hawthorne",
        classroom="Unity 1",
        position_name="Aide 1",
        position_type="Aide",
        status="filled",
        person_name="Sofia Ramirez",
        permit_status="teacher_permit_approved",
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE assignments SET current_opened_date = '2026-06-01T00:00:00Z' WHERE id = ?",
            (old_open_id,),
        )
        conn.execute(
            "UPDATE assignments SET start_date = '' WHERE id = ?",
            (coming_id,),
        )
    before_assignments = store.list_assignments()
    before_history = store.list_assignment_history()
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing_seed.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()

    validation_nav = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationNavButton")
    assert validation_nav.isEnabled()
    validation_nav.click()
    app.processEvents()

    assert page.findChild(qt_widgets.QWidget, "StaffingV2ValidationDashboard") is not None
    assert validation_nav.property("staffingV2ActiveNav") is True
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ValidationTitle").text() == "Staffing Validation"
    assert "Review staffing compliance" in page.findChild(qt_widgets.QLabel, "StaffingV2ValidationSubtitle").text()
    validation_export = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationExportButton")
    assert validation_export.text() == "Export Report"
    assert not validation_export.icon().isNull()
    assert validation_export.isEnabled()
    validation_export.click()
    app.processEvents()
    validation_export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ValidationExportDialog")
    assert validation_export_dialog is not None
    validation_export_text = _widget_text(validation_export_dialog)
    assert "Export Validation Report" in validation_export_text
    assert "Total issues 2" in validation_export_text
    assert "Critical 1" in validation_export_text
    assert "Unfilled Need Now position" in validation_export_text
    assert "Coming position missing start date" in validation_export_text
    validation_export_dialog.accept()
    app.processEvents()
    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2ValidationMetricCard"))
    assert "Total Issues 2" in metric_text
    assert "Critical 1" in metric_text
    assert "Warning 1" in metric_text
    assert "Info 0" in metric_text
    assert "Overall Compliance" in metric_text
    validation_tabs = page.findChild(qt_widgets.QFrame, "StaffingV2ValidationTabs")
    assert validation_tabs is not None
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationAllIssuesTab").text() == "All Issues (2)"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationCriticalTab").text() == "Critical (1)"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationWarningsTab").text() == "Warnings (1)"
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationInfoTab").text() == "Info (0)"
    all_issues_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationAllIssuesTab")
    critical_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationCriticalTab")
    warning_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationWarningsTab")
    info_tab = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationInfoTab")
    assert all_issues_tab.property("staffingV2ActiveValidationTab") is True
    assert critical_tab.property("staffingV2ActiveValidationTab") is False
    assert warning_tab.property("staffingV2ActiveValidationTab") is False
    assert info_tab.property("staffingV2ActiveValidationTab") is False
    validation_search = page.findChild(qt_widgets.QLineEdit, "StaffingV2ValidationSearch")
    assert validation_search.placeholderText() == "Search issues..."
    assert validation_search.actions()
    filters = page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationFiltersButton")
    assert filters.text() == "Filters 3"
    assert filters.property("staffingV2FilterActiveCount") == 3
    assert not filters.icon().isNull()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ValidationTable")
    validation_table_width = table.width()
    assert table.rowCount() == 2
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert {
        "Unfilled Need Now position",
        "Coming position missing start date",
        "Harmony 1",
        "Tranquility",
        "Critical",
        "Warning",
    } <= table_text
    for row in range(table.rowCount()):
        view_button = table.cellWidget(row, 6)
        assert isinstance(view_button, qt_widgets.QPushButton)
        assert view_button.text() == "View"
        assert not view_button.icon().isNull()
        assert view_button.isEnabled()
    critical_issue_row = next(
        row for row in range(table.rowCount()) if table.item(row, 0).text() == "Unfilled Need Now position"
    )
    critical_view = table.cellWidget(critical_issue_row, 6)
    assert isinstance(critical_view, qt_widgets.QPushButton)
    critical_view.click()
    app.processEvents()
    drawer = page.findChild(qt_widgets.QFrame, "StaffingV2PositionDrawer")
    assert drawer is not None
    assert drawer.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2Dashboard")
    assert table.width() == validation_table_width
    drawer_scroll = drawer.findChild(qt_widgets.QScrollArea, "StaffingV2PositionDrawerScroll")
    assert drawer_scroll is not None
    assert drawer_scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert drawer_scroll.widget().findChild(qt_widgets.QPushButton, "StaffingV2DrawerSaveChanges") is None
    drawer_footer = drawer.findChild(qt_widgets.QWidget, "StaffingV2PositionDrawerFooter")
    assert drawer_footer is not None
    assert drawer_footer.findChild(qt_widgets.QPushButton, "StaffingV2DrawerSaveChanges") is not None
    assert drawer.findChild(qt_widgets.QLabel, "StaffingV2DrawerPositionName").text() == "Teacher 1"
    drawer_text = _widget_text(drawer)
    assert "Harmony 1" in drawer_text
    assert "Need Now" in drawer_text
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ValidationResultCount").text() == "Showing 1 to 2 of 2 issues"
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2ValidationRowsPerPage").currentText() == "10 / page"
    critical_tab.click()
    app.processEvents()
    assert table.rowCount() == 1
    assert table.item(0, 3).text() == "Critical"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ValidationResultCount").text() == "Showing 1 to 1 of 1 issues"
    assert all_issues_tab.property("staffingV2ActiveValidationTab") is False
    assert critical_tab.property("staffingV2ActiveValidationTab") is True
    all_issues_tab.click()
    app.processEvents()
    assert table.rowCount() == 2
    assert all_issues_tab.property("staffingV2ActiveValidationTab") is True
    assert critical_tab.property("staffingV2ActiveValidationTab") is False
    right_panel = page.findChild(qt_widgets.QFrame, "StaffingV2ValidationRightPanel")
    assert right_panel.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2ValidationDashboard")
    right_scroll = right_panel.findChild(qt_widgets.QScrollArea, "StaffingV2ValidationRightPanelScroll")
    assert right_scroll is not None
    assert right_scroll.verticalScrollBarPolicy() == qt_core.Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    right_panel_text = _widget_text(right_panel)
    assert "Compliance Summary" in right_panel_text
    assert "Quick Actions" in right_panel_text
    quick_actions = {
        "StaffingV2ValidationRunFullButton": "Run Full Validation",
        "StaffingV2ValidationExportQuickButton": "Export Validation Report",
        "StaffingV2ValidationRulesButton": "View Validation Rules",
    }
    for object_name, text in quick_actions.items():
        action_button = page.findChild(qt_widgets.QPushButton, object_name)
        assert action_button is not None
        assert action_button.text() == text
        assert not action_button.icon().isNull()
        assert action_button.isEnabled()
    page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationExportQuickButton").click()
    app.processEvents()
    quick_export_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ValidationExportDialog")
    assert quick_export_dialog is not None
    assert "Export Validation Report" in _widget_text(quick_export_dialog)
    quick_export_dialog.accept()
    app.processEvents()
    page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationRulesButton").click()
    app.processEvents()
    rules_dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2ValidationRulesDialog")
    assert rules_dialog is not None
    rules_text = _widget_text(rules_dialog)
    assert "Validation Rules" in rules_text
    assert "Coverage" in rules_text
    assert "Permit Status" in rules_text
    assert "Upcoming Start Dates" in rules_text
    rules_dialog.accept()
    app.processEvents()
    with store.connect() as conn:
        conn.execute(
            "UPDATE assignments SET start_date = '2026-07-20' WHERE id = ?",
            (coming_id,),
        )
    page.findChild(qt_widgets.QPushButton, "StaffingV2ValidationRunFullButton").click()
    app.processEvents()
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2ValidationTable")
    refreshed_issues = {
        table.item(row, 0).text()
        for row in range(table.rowCount())
        if table.item(row, 0) is not None
    }
    assert "Coming position missing start date" not in refreshed_issues
    assert refreshed_issues == {"Unfilled Need Now position"}
    assert page.findChild(qt_widgets.QLabel, "StaffingV2ValidationResultCount").text() == "Showing 1 to 1 of 1 issues"
    assert "About Validation" in right_panel_text

    drawer = page.findChild(qt_widgets.QFrame, "StaffingV2FilterDrawer")
    assert drawer is not None
    assert drawer.isHidden()
    filters.click()
    app.processEvents()
    assert not drawer.isHidden()
    assert drawer.parent() is page.findChild(qt_widgets.QWidget, "StaffingV2ValidationDashboard")
    assert table.width() == validation_table_width
    filter_scroll = drawer.findChild(qt_widgets.QScrollArea, "StaffingV2FilterDrawerScroll")
    assert filter_scroll is not None
    assert filter_scroll.widget().findChild(qt_widgets.QPushButton, "StaffingV2FilterApplyButton") is None
    filter_footer = drawer.findChild(qt_widgets.QWidget, "StaffingV2FilterDrawerFooter")
    assert filter_footer is not None
    drawer_text = _widget_text(drawer)
    assert "Filters" in drawer_text
    assert "School" in drawer_text
    assert "Severity" in drawer_text
    assert "Issue Type" in drawer_text
    assert "Detected Date" in drawer_text
    assert page.findChild(qt_widgets.QComboBox, "StaffingV2FilterDetectedDate").currentText() == "Last 30 Days"
    reset_button = page.findChild(qt_widgets.QPushButton, "StaffingV2FilterResetButton")
    assert reset_button.text() == "Reset"
    assert not reset_button.icon().isNull()
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2FilterCloseButton").text() == ""
    apply_button = page.findChild(qt_widgets.QPushButton, "StaffingV2FilterApplyButton")
    assert apply_button.text() == "Apply Filters 3"
    assert apply_button.property("staffingV2FilterActiveCount") == 3
    assert not apply_button.icon().isNull()
    assert filter_footer.findChild(qt_widgets.QPushButton, "StaffingV2FilterApplyButton") is apply_button
    assert page.findChild(qt_widgets.QPushButton, "StaffingV2FilterCancelButton").text() == "Cancel"
    page.findChild(qt_widgets.QPushButton, "StaffingV2FilterCancelButton").click()
    app.processEvents()
    assert drawer.isHidden()
    assert len(store.list_assignments()) == len(before_assignments)
    assert len(store.list_assignment_history()) == len(before_history)
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_history_offer_actions_advance_generated_and_approved_rows(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "candidate_email": "latoya@example.org",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "outcome": "Hire",
            "offer_status": "generated",
            "offer_path": str(tmp_path / "Latoya Nugent Offer.docx"),
        }
    )
    notifications = []

    class FakeNotifications:
        def emit_event(self, event_type, payload, idempotency_key):
            notifications.append((event_type, payload, idempotency_key))
            return []

    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.notification_service = FakeNotifications()

    window._open_history_offer(model.home.history_rows[0])
    approved_rows = InterviewHistoryStore(history_path).load()
    approved_model_row = pyside_interview_app._build_pyside_history_rows(history_path)[0]
    window._open_history_offer(approved_model_row)
    accepted_rows = InterviewHistoryStore(history_path).load()

    assert approved_rows[0]["offer_status"] == "approved"
    assert accepted_rows[0]["offer_status"] == "accepted"
    assert [event[0] for event in notifications] == ["offer.approved", "offer.accepted"]
    approved_payload = notifications[0][1]
    assert approved_payload["candidate"] == "Latoya Nugent"
    assert approved_payload["candidate_email"] == "latoya@example.org"
    assert approved_payload["school_code"] == "PMD"
    assert approved_payload["school_location"] == "Palmdale"
    assert approved_payload["reply_by_date"] == (date.today() + timedelta(days=3)).isoformat()
    assert approved_payload["offer_path"].endswith("Latoya Nugent Offer.docx")
    window.window.close()
    app.processEvents()

def test_pyside_staffing_open_action_refreshes_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "dont_need_now"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    table = window.window.findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")
    button = _staffing_button_for_position(table, "Teacher 1")

    assert button.text() == "Open"
    button.click()
    app.processEvents()

    table = window.window.findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")
    labels = [label.text() for label in window.stack.widget(3).findChildren(qt_widgets.QLabel)]
    row = _staffing_row_for_position(table, "Teacher 1")
    assert table.item(row, 3).text() == "need_now"
    assert table.cellWidget(row, 7).text() == "Mark Coming"
    assert any("Open positions: 1" in text for text in labels)
    window.window.close()
    app.processEvents()

def test_pyside_director_staffing_mode_uses_same_staffing_page_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "dont_need_now"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    director_model = pyside_interview_app.build_director_staffing_model(full_model)
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    page = window.stack.widget(0)
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    action_button = table.cellWidget(0, 7)

    assert director_model.navigation == ["Staffing v2"]
    assert isinstance(window.staffing_v2_dashboard, StaffingDashboardV2Page)
    assert window.staffing_v2_dashboard.school_filter == ""
    assert window.window.windowTitle() == "Director Staffing Dashboard"
    staffing_sidebar = page.findChild(qt_widgets.QFrame, "StaffingV2Sidebar")

    assert window.sidebar_panel.isHidden()
    assert staffing_sidebar is not None
    assert "Launch Pad Learning" in _widget_text(staffing_sidebar)
    assert window.sidebar.currentRow() == 0
    assert window.stack.count() == 1
    assert page.objectName() == "PySideStaffingV2Page"
    assert page.findChild(qt_widgets.QLabel, "StaffingV2PageTitle").text() == "Staffing Dashboard"
    assert table is not None
    assert action_button.text() == "Mark Need Now"
    window.window.close()
    app.processEvents()

def test_pyside_director_staffing_mode_filters_to_assigned_school(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Hawthorne Teacher", "position_type": "Teacher", "status": "need_now"}
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Harmony",
                                "positions": [
                                    {"position_name": "Palmdale Teacher", "position_type": "Teacher", "status": "need_now"},
                                    {"position_name": "Palmdale Aide", "position_type": "Aide", "status": "filled", "person": {"name": "Koryn"}},
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "Palmdale"],
    )

    director_model = pyside_interview_app.build_director_staffing_model(full_model, school="Palmdale")
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    page = window.stack.widget(0)
    selector = page.findChild(qt_widgets.QComboBox, "StaffingV2SchoolFilter")
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")
    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2MetricCard"))
    board_text = _widget_text(page)

    assert director_model.director_staffing_school == "Palmdale"
    assert isinstance(window.staffing_v2_dashboard, StaffingDashboardV2Page)
    assert window.staffing_v2_dashboard.school_filter == "Palmdale"
    assert [selector.itemText(index) for index in range(selector.count())] == ["Palmdale"]
    assert selector.currentText() == "Palmdale"
    assert table.rowCount() == 2
    assert "Harmony" in board_text
    assert "OPEN POSITION" in board_text
    assert "Koryn" in board_text
    assert "Tranquility" not in board_text
    assert "Open positions: 1" in metric_text
    window.window.close()
    app.processEvents()

def test_staffing_db_path_for_school_uses_safe_school_specific_names(tmp_path: Path) -> None:
    base_path = tmp_path / "staffing_dashboard.sqlite3"

    assert pyside_interview_app.staffing_db_path_for_school("Hawthorne", base_path=base_path) == (
        tmp_path / "staffing_dashboard_hawthorne.sqlite3"
    )
    assert pyside_interview_app.staffing_db_path_for_school("Long Beach / Bixby", base_path=base_path) == (
        tmp_path / "staffing_dashboard_long_beach_bixby.sqlite3"
    )
    assert pyside_interview_app.staffing_db_path_for_school("", base_path=base_path) == base_path

def test_school_specific_staffing_db_bootstraps_from_existing_base_db(tmp_path: Path) -> None:
    base_path = tmp_path / "staffing_dashboard.sqlite3"
    base_store = pyside_interview_app.StaffingStore(base_path)
    base_store.initialize()
    base_store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Existing Palmdale Teacher",
        position_type="Teacher",
        status="need_now",
    )
    school_path = pyside_interview_app.staffing_db_path_for_school("Palmdale", base_path=base_path)

    pyside_interview_app._bootstrap_school_staffing_db_from_base("Palmdale", school_path, base_path=base_path)

    school_store = pyside_interview_app.StaffingStore(school_path)
    assignments = school_store.list_assignments()
    assert school_path.exists()
    assert [assignment.position_name for assignment in assignments] == ["Existing Palmdale Teacher"]


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_director_staffing_mode_uses_school_specific_db_when_other_school_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [{"position_name": "Hawthorne Teacher", "position_type": "Teacher"}],
                            }
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Harmony",
                                "positions": [{"position_name": "Palmdale Teacher", "position_type": "Teacher"}],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    base_path = tmp_path / "staffing.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", base_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    hawthorne_path = pyside_interview_app.staffing_db_path_for_school("Hawthorne", base_path=base_path)
    hawthorne_path.parent.mkdir(parents=True, exist_ok=True)
    hawthorne_path.with_suffix(hawthorne_path.suffix + ".editing.lock").write_text(
        '{"owner": "hawthorne-director", "created_at": "2099-01-01T00:00:00Z"}',
        encoding="utf-8",
    )
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "Palmdale"],
    )

    director_model = pyside_interview_app.build_director_staffing_model(full_model, school="Palmdale")
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2PositionsTable")

    assert window.staffing_store.path == pyside_interview_app.staffing_db_path_for_school("Palmdale", base_path=base_path)
    assert table.rowCount() == 1
    assert "Palmdale Teacher" in _widget_text(window.window)
    assert "Hawthorne Teacher" not in _widget_text(window.window)
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
def test_interview_finalize_queues_director_referral_without_staffing_db_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    staffing_path = tmp_path / "staffing.sqlite3"
    referral_queue_path = tmp_path / "staffing_referrals.pending.jsonl"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", staffing_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_REFERRAL_QUEUE_PATH", referral_queue_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    window.session = SimpleNamespace(
        candidate_name="Queued Candidate",
        school="Palmdale",
        position="Teacher",
        interview_date="2026-07-08",
    )

    window._record_staffing_director_referral_from_finalize_result(
        {
            "history_id": "hist-queued",
            "scoring": {"outcome": "Hire", "interviewer_rating": 8.8},
        }
    )

    assert not staffing_path.exists()
    assert referral_queue_path.exists()
    queued = [json.loads(line) for line in referral_queue_path.read_text(encoding="utf-8").splitlines()]
    assert queued[0]["payload"]["history_id"] == "hist-queued"
    assert queued[0]["payload"]["school"] == "Palmdale"
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_director_staffing_poll_imports_queued_referral_and_refreshes_gui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps({"schools": [{"name": "Palmdale", "classrooms": [{"name": "Harmony", "positions": []}]}]}),
        encoding="utf-8",
    )
    base_path = tmp_path / "staffing.sqlite3"
    referral_queue_path = tmp_path / "staffing_referrals.pending.jsonl"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", base_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_REFERRAL_QUEUE_PATH", referral_queue_path)
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Palmdale"],
    )
    director_model = pyside_interview_app.build_director_staffing_model(full_model, school="Palmdale")
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")
    assert table.rowCount() == 0
    pyside_interview_app._append_staffing_referral_queue(
        {
            "history_id": "hist-live",
            "candidate_name": "Live Queue Candidate",
            "school": "Palmdale",
            "position": "Teacher",
            "interviewer_rating": 8.5,
            "interviewer_outcome": "hire",
            "interview_date": "2026-07-09",
            "candidate_email": "",
            "referral_date": "2026-07-09",
        }
    )

    window._poll_staffing_referral_queue()
    app.processEvents()

    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Live Queue Candidate"
    assert not referral_queue_path.exists()
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_director_staffing_poll_imports_review_score_dismissal_and_removes_pending_referral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    staffing_path = tmp_path / "staffing.sqlite3"
    referral_queue_path = tmp_path / "staffing_referrals.pending.jsonl"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", staffing_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_REFERRAL_QUEUE_PATH", referral_queue_path)
    school_store = pyside_interview_app.StaffingStore(
        pyside_interview_app.staffing_db_path_for_school("Palmdale", base_path=staffing_path)
    )
    school_store.initialize()
    pyside_interview_app.StaffingService(school_store).upsert_director_candidate_referral(
        history_id="hist-review-remove",
        candidate_name="Remove Candidate",
        school="Palmdale",
        position="Teacher",
        interviewer_rating=7.0,
        interviewer_outcome="borderline",
        interview_date="2026-07-10",
    )
    full_model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Palmdale"],
    )
    director_model = pyside_interview_app.build_director_staffing_model(full_model, school="Palmdale")
    window = pyside_interview_app.PySideInterviewWindow(director_model)
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")
    assert table.rowCount() == 1

    pyside_interview_app._append_staffing_referral_dismissal_queue(
        {
            "history_id": "hist-review-remove",
            "candidate_name": "Remove Candidate",
            "school": "Palmdale",
            "position": "Teacher",
        }
    )
    window._poll_staffing_referral_queue()
    app.processEvents()

    assert table.rowCount() == 0
    assert "hist-review-remove" in school_store.list_dismissed_director_referral_history_ids()
    window.window.close()
    app.processEvents()

def test_pyside_staffing_v2_summary_cards_follow_selected_school(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Hawthorne Teacher", "position_type": "Teacher", "status": "need_now"}
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Harmony",
                                "positions": [
                                    {"position_name": "Palmdale Teacher", "position_type": "Teacher", "status": "need_now"},
                                    {"position_name": "Palmdale Aide", "position_type": "Aide", "status": "need_now"},
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    selector = page.findChild(qt_widgets.QComboBox, "StaffingV2SchoolFilter")

    selector.setCurrentText("Hawthorne")
    app.processEvents()

    metric_text = " ".join(_widget_text(card) for card in page.findChildren(qt_widgets.QFrame, "StaffingV2MetricCard"))
    assert "Schools: 1" in metric_text
    assert "Open positions: 1" in metric_text
    assert "Open positions: 3" not in metric_text
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_pyside_staffing_v2_director_candidates_follow_admin_school_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony",
                                "positions": [
                                    {
                                        "position_name": "Hawthorne Teacher",
                                        "position_type": "Teacher",
                                        "status": "need_now",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "name": "North Long Beach",
                        "classrooms": [
                            {
                                "name": "Unity",
                                "positions": [
                                    {
                                        "position_name": "North Long Beach Teacher",
                                        "position_type": "Teacher",
                                        "status": "need_now",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Quest",
                                "positions": [
                                    {
                                        "position_name": "Palmdale Teacher",
                                        "position_type": "Teacher",
                                        "status": "need_now",
                                    }
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    staffing_path = tmp_path / "staffing.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", staffing_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    store = pyside_interview_app.StaffingStore(staffing_path)
    store.initialize()
    store.import_seed_file(seed_path)
    service = pyside_interview_app.StaffingService(store)
    for school, candidate in [
        ("Hawthorne", "Hawthorne Candidate"),
        ("North Long Beach", "North Long Beach Candidate"),
        ("Palmdale", "Palmdale Candidate"),
    ]:
        service.upsert_director_candidate_referral(
            history_id=f"hist-{school.lower().replace(' ', '-')}",
            candidate_name=candidate,
            school=school,
            position="Teacher",
            interviewer_rating=8.0,
            interviewer_outcome="hire",
            interview_date="2026-07-09",
        )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "North Long Beach", "Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model, defer_secondary_pages=True)
    nav_items = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]
    window.sidebar.setCurrentRow(nav_items.index("Staffing v2"))
    app.processEvents()
    page = window.stack.currentWidget()
    selector = page.findChild(qt_widgets.QComboBox, "StaffingV2SchoolFilter")
    table = page.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")

    selector.setCurrentText("Hawthorne")
    app.processEvents()
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["Hawthorne Candidate"]
    assert [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())] == [
        "Candidate",
        "Outcome",
        "Score",
        "Interview\nDate",
        "Role",
        "Referral\nDate",
        "Action",
    ]
    assert not table.horizontalHeader().stretchLastSection()
    assert table.horizontalHeader().minimumHeight() >= 54
    assert table.columnWidth(1) == 84
    assert table.columnWidth(2) == 64
    assert table.columnWidth(3) == 92
    assert table.columnWidth(4) == 188
    assert table.columnWidth(6) >= 156
    record_button = table.cellWidget(0, 6)
    assert record_button is not None
    assert record_button.minimumWidth() >= 144
    assert table.columnWidth(6) >= record_button.minimumWidth()

    selector.setCurrentText("North Long Beach")
    app.processEvents()
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["North Long Beach Candidate"]

    selector.setCurrentText("Palmdale")
    app.processEvents()
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["Palmdale Candidate"]
    window.window.close()
    app.processEvents()

def test_staffing_v2_director_pending_table_uses_compact_readable_columns(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_gui = pytest.importorskip("PySide6.QtGui")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps({"schools": [{"name": "Hawthorne", "classrooms": [{"name": "Harmony", "positions": []}]}]}),
        encoding="utf-8",
    )
    store = pyside_interview_app.StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    store.import_seed_file(seed_path)
    service = pyside_interview_app.StaffingService(store)
    service.upsert_director_candidate_referral(
        history_id="hist-compact-columns",
        candidate_name="Adrianna Love",
        school="Hawthorne",
        position="behavior_support_specialist",
        interviewer_rating=9.75,
        interviewer_outcome="hire",
        interview_date="2026-07-02",
    )

    page = StaffingDashboardV2Page(
        QtCore=qt_core,
        QtGui=qt_gui,
        QtWidgets=qt_widgets,
        store=store,
        service_factory=lambda: service,
        school_filter="Hawthorne",
        notification_store_path=tmp_path / "notification_rules.sqlite3",
    )
    table = page.widget.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")

    assert [table.item(0, column).text() for column in range(1, 5)] == [
        "Hire",
        "9.75",
        "2026-07-02",
        "behavior_support_specialist",
    ]
    assert [table.columnWidth(column) for column in range(1, 5)] == [84, 64, 92, 188]
    assert table.item(0, 4).toolTip() == "behavior_support_specialist"
    page.widget.close()
    app.processEvents()

def test_pyside_staffing_action_button_exposes_secondary_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "coming", "start_date": "2026-07-03", "person": {"name": "Jane Doe"}},
                                    {"position_name": "Teacher 2", "position_type": "Teacher", "status": "replace", "person": {"name": "Angie"}},
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    table = window.window.findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")

    coming_menu_labels = [action.text() for action in _staffing_button_for_position(table, "Teacher 1").menu().actions()]
    replace_menu_labels = [action.text() for action in _staffing_button_for_position(table, "Teacher 2").menu().actions()]

    assert "Revert Coming" in coming_menu_labels
    assert "Mark Not Needed" in coming_menu_labels
    assert "Clear Replacement" in replace_menu_labels
    assert "Mark Not Needed" in replace_menu_labels
    window.window.close()
    app.processEvents()

def test_pyside_staffing_dashboard_groups_by_school_and_colors_statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {"name": "Hawthorne", "classrooms": [{"name": "Tranquility", "positions": [{"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"}]}]},
                    {"name": "Palmdale", "classrooms": [{"name": "Harmony", "positions": [{"position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Angie", "permit_status": "teacher_permit_approved"}}]}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "Palmdale"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    tabs = window.window.findChild(qt_widgets.QTabWidget, "PySideStaffingSchoolTabs")
    first_table = tabs.widget(0).findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")
    second_table = tabs.widget(1).findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")

    assert [tabs.tabText(index) for index in range(tabs.count())] == ["Hawthorne", "Palmdale"]
    assert "Need Now: 1" in tabs.widget(0).findChild(qt_widgets.QLabel, "PySideStaffingSummary").text()
    assert first_table.item(_staffing_row_for_position(first_table, "Teacher 1"), 3).background().color().isValid()
    second_row = _staffing_row_for_position(second_table, "Teacher 1")
    assert second_table.item(second_row, 2).text() == "Angie"
    assert second_table.item(second_row, 2).background().color().isValid()
    window.window.close()
    app.processEvents()

def test_pyside_staffing_dashboard_renders_workbook_layout_tabs_and_actual_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "display_order": 1,
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "ratio_group": "3 to 1 (infant units needed)",
                                "licensed_capacity": 12,
                                "slots": [
                                    {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Angie", "permit_status": "teacher_permit_approved"}},
                                    {"slot_group": "teacher", "position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now", "notes": "?"},
                                ],
                            }
                        ],
                        "support_rows": [
                            {"name": "Infant Floater", "slots": [{"slot_group": "support", "position_name": "Infant Floater", "position_type": "Support", "status": "filled", "person": {"name": "Amy"}, "notes": "Full time"}]}
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "display_order": 2,
                        "classrooms": [
                            {
                                "name": "Harmony",
                                "ratio_group": "4 to 1",
                                "licensed_capacity": 24,
                                "slots": [
                                    {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Madisan"}},
                                    {"slot_group": "aide", "position_name": "Aide 1", "position_type": "Aide", "status": "coming", "person": {"name": "Koryn"}},
                                ],
                            }
                        ],
                    },
                    {
                        "name": "North Long Beach",
                        "display_order": 3,
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "ratio_group": "3 to 1 (infant units needed)",
                                "licensed_capacity": 16,
                                "slots": [
                                    {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Naomi*"}},
                                    {"slot_group": "aide", "position_name": "Aide 1", "position_type": "Aide", "status": "filled", "person": {"name": "Ruby"}},
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    tabs = window.window.findChild(qt_widgets.QTabWidget, "PySideStaffingSchoolTabs")
    selector = window.window.findChild(qt_widgets.QComboBox, "PySideStaffingSchoolSelector")
    hawthorne = tabs.widget(0)
    board = hawthorne.findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")
    labels = [label.text() for label in hawthorne.findChildren(qt_widgets.QLabel)]

    assert [tabs.tabText(index) for index in range(tabs.count())] == ["Hawthorne", "Palmdale", "North Long Beach"]
    assert [selector.itemText(index) for index in range(selector.count())] == ["Hawthorne", "Palmdale", "North Long Beach"]
    selector.setCurrentIndex(2)
    app.processEvents()
    assert tabs.currentIndex() == 2
    assert board is not None
    board_text = {
        board.item(row, column).text()
        for row in range(board.rowCount())
        for column in range(board.columnCount())
        if board.item(row, column) is not None
    }
    assert {"Tranquility", "Angie", "OPEN POSITION", "12", "Infant Floater", "Amy", "Full time"} <= board_text
    assert "need_now" in board_text
    assert any("3 to 1 (infant units needed)" in text for text in labels)
    window.window.close()
    app.processEvents()

def test_pyside_staffing_dashboard_refreshes_partial_seed_and_hides_color_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "slots": [
                                    {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Angie"}},
                                    {"slot_group": "teacher", "position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now"},
                                ],
                            },
                            {
                                "name": "Harmony",
                                "slots": [
                                    {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Brenda"}}
                                ],
                            },
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Unity",
                                "slots": [
                                    {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Cora"}}
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "staffing.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)

    partial_store = pyside_interview_app.StaffingStore(db_path)
    partial_store.initialize()
    partial_store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
        status="filled",
        person_name="Angie",
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = _pyside_window_on_page(model, "Staffing")
    tabs = window.window.findChild(qt_widgets.QTabWidget, "PySideStaffingSchoolTabs")
    selector = window.window.findChild(qt_widgets.QComboBox, "PySideStaffingSchoolSelector")
    labels = [label.text() for label in window.stack.widget(3).findChildren(qt_widgets.QLabel)]
    hawthorne_board = tabs.widget(0).findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")

    assert [tabs.tabText(index) for index in range(tabs.count())] == ["Hawthorne", "Palmdale"]
    assert [selector.itemText(index) for index in range(selector.count())] == ["Hawthorne", "Palmdale"]
    board_text = {
        hawthorne_board.item(row, column).text()
        for row in range(hawthorne_board.rowCount())
        for column in range(hawthorne_board.columnCount())
        if hawthorne_board.item(row, column) is not None
    }
    assert {"Tranquility", "Harmony", "Angie", "Brenda", "OPEN POSITION"} <= board_text
    assert "Color Code Key" not in labels
    assert "Need Now - Job Opening" not in labels
    window.window.close()
    app.processEvents()

def test_pyside_staffing_classroom_detail_matches_dashboard_mockup_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "ratio_group": "Preschool",
                                "licensed_capacity": 18,
                                "slots": [
                                    {
                                        "slot_group": "teacher",
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Imgard M.", "permit_status": "teacher_permit_approved"},
                                        "start_date": "2025-03-03",
                                    },
                                    {
                                        "slot_group": "teacher",
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "status": "replace",
                                        "person": {"name": "Angie R.", "permit_status": "permit_in_process"},
                                        "start_date": "2025-05-01",
                                    },
                                    {
                                        "slot_group": "teacher",
                                        "position_name": "Teacher 3",
                                        "position_type": "Teacher",
                                        "status": "coming",
                                        "person": {"name": "Denise A.", "permit_status": "teacher_permit_approved"},
                                        "start_date": "2025-05-19",
                                    },
                                    {
                                        "slot_group": "aide",
                                        "position_name": "Aide 1",
                                        "position_type": "Aide",
                                        "status": "need_now",
                                        "current_opened_date": "2025-05-14",
                                    },
                                ],
                            },
                            {
                                "name": "Quest",
                                "ratio_group": "Preschool",
                                "licensed_capacity": 16,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Sarah M."}}
                                ],
                            },
                            {
                                "name": "Unity",
                                "ratio_group": "Preschool",
                                "licensed_capacity": 18,
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "replace", "person": {"name": "Mia"}},
                                ],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = _pyside_window_on_page(model, "Staffing")
    page = window.stack.widget(3)
    school_selector = page.findChild(qt_widgets.QComboBox, "PySideStaffingSchoolSelector")
    classroom_selector = page.findChild(qt_widgets.QComboBox, "PySideStaffingClassroomSelector")
    classroom_list = page.findChild(qt_widgets.QListWidget, "PySideStaffingClassroomList")
    section_splitter = page.findChild(qt_widgets.QSplitter, "PySideStaffingSectionSplitter")
    cards = page.findChildren(qt_widgets.QFrame, "PySideStaffingMetricCard")
    table = page.findChild(qt_widgets.QTableWidget, "PySideStaffingPositionsTable")
    title = page.findChild(qt_widgets.QLabel, "PySideStaffingClassroomTitle")
    priority = page.findChild(qt_widgets.QLabel, "PySideStaffingPriorityBadge")

    assert window.stack.widget(3).findChild(qt_widgets.QLabel, "Title").text() == "Classroom Detail"
    assert school_selector.currentText() == "Hawthorne"
    assert classroom_selector.currentText() == "Harmony 1"
    assert [classroom_selector.itemText(index) for index in range(classroom_selector.count())] == ["Harmony 1", "Quest", "Unity"]
    assert [classroom_list.item(index).text() for index in range(classroom_list.count())] == [
        "Harmony 1\nNeed: 1 - Replace: 1 - Filled: 1 - Don't Need: 0",
        "Quest\nNeed: 0 - Replace: 0 - Filled: 1 - Don't Need: 0",
        "Unity\nNeed: 0 - Replace: 1 - Filled: 0 - Don't Need: 0",
    ]
    assert section_splitter is not None
    assert section_splitter.count() == 3
    assert not section_splitter.childrenCollapsible()
    assert section_splitter.widget(0).maximumWidth() > 320
    assert section_splitter.widget(2).minimumWidth() == 300
    assert classroom_list.minimumWidth() >= 360
    assert classroom_list.wordWrap()
    assert classroom_list.item(0).background().color().name().upper() == "#FEF08A"
    assert classroom_list.item(1).background().color().name().upper() == "#BBF7D0"
    assert classroom_list.item(2).background().color().name().upper() == "#FF0000"
    assert title.text() == "Harmony 1"
    assert priority.text() == "Need Now"
    assert len(cards) == 6
    assert [
        table.horizontalHeaderItem(index).text()
        for index in range(table.columnCount())
    ] == ["Position", "Person", "Status", "Start Date", "Days Open", "Permit Status", "Action"]
    table_text = {
        table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    assert {"Teacher 1", "Imgard M.", "Filled", "Teacher Permit Approved", "Aide 1", "OPEN POSITION", "Need Now"} <= table_text
    assert _staffing_button_for_position(table, "Aide 1").text() == "Mark Coming"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_row_click_opens_mockup_detail_drawer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Quest",
                                "slots": [
                                    {
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Sarah M.", "permit_status": "teacher_permit_approved"},
                                        "start_date": "2025-01-15",
                                    },
                                    {
                                        "position_name": "Teacher 3",
                                        "position_type": "Teacher",
                                        "status": "need_now",
                                        "current_opened_date": "2025-05-14",
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    table = window.staffing_positions_table
    drawer = window.staffing_detail_drawer

    table.cellClicked.emit(_staffing_row_for_position(table, "Teacher 3"), 0)
    app.processEvents()

    drawer_labels = [label.text() for label in drawer.findChildren(qt_widgets.QLabel)]
    drawer_buttons = [button.text() for button in drawer.findChildren(qt_widgets.QPushButton)]
    assert not drawer.isHidden()
    assert "Position Details" in drawer_labels
    assert "OPEN POSITION" in drawer_labels
    assert "Teacher 3" in drawer_labels
    assert "Need Now" in drawer_labels
    assert {"Edit", "Save", "Mark Coming", "Don't Need Now", "Close"} <= set(drawer_buttons)

    position_type = drawer.findChild(qt_widgets.QComboBox, "PySideStaffingDetailPositionType")
    status = drawer.findChild(qt_widgets.QComboBox, "PySideStaffingDetailStatus")
    program = drawer.findChild(qt_widgets.QComboBox, "PySideStaffingDetailProgram")
    start_date = drawer.findChild(qt_widgets.QDateEdit, "PySideStaffingDetailStartDate")
    shift_start = drawer.findChild(qt_widgets.QTimeEdit, "PySideStaffingDetailShiftStart")
    notes = drawer.findChild(qt_widgets.QTextEdit, "PySideStaffingDetailNotes")
    save = drawer.findChild(qt_widgets.QPushButton, "PySideStaffingDetailSave")
    assert position_type.currentText() == "Teacher"
    assert status.currentText() == "Need Now"
    assert start_date.specialValueText() == "-"
    assert shift_start.displayFormat() == "h:mm AP"

    position_type.setCurrentText("Aide")
    program.setCurrentText("Toddler")
    notes.setPlainText("Needs bilingual coverage.")
    save.click()
    app.processEvents()

    updated = window.staffing_store.get_assignment(
        next(row.id for row in window.staffing_store.list_assignments() if row.position_name == "Teacher 3")
    )
    assert updated.position_type == "Aide"
    assert updated.classroom_program == "Toddler"
    assert updated.notes == "Needs bilingual coverage."

    drawer = window.staffing_detail_drawer
    filled_id = next(row.id for row in window.staffing_store.list_assignments() if row.position_name == "Teacher 1")
    window._open_staffing_assignment_details(filled_id)
    app.processEvents()

    drawer_labels = [label.text() for label in drawer.findChildren(qt_widgets.QLabel)]
    drawer_buttons = [button.text() for button in drawer.findChildren(qt_widgets.QPushButton)]
    assert "Person Details" in drawer_labels
    assert "Sarah M." in drawer_labels
    assert "Teacher Permit Approved" in drawer_labels
    assert {"Replace", "Edit", "Save", "Close"} <= set(drawer_buttons)

    person_name = drawer.findChild(qt_widgets.QLineEdit, "PySideStaffingDetailPersonName")
    position_name = drawer.findChild(qt_widgets.QLineEdit, "PySideStaffingDetailPositionName")
    permit = drawer.findChild(qt_widgets.QComboBox, "PySideStaffingDetailPermitStatus")
    filled_notes = drawer.findChild(qt_widgets.QTextEdit, "PySideStaffingDetailNotes")
    filled_save = drawer.findChild(qt_widgets.QPushButton, "PySideStaffingDetailSave")
    person_name.setText("Sara M.")
    position_name.setText("Lead Teacher")
    permit.setCurrentText("permit_in_process")
    filled_notes.setPlainText("Moved to lead slot.")
    filled_save.click()
    app.processEvents()

    filled = window.staffing_store.get_assignment(filled_id)
    assert filled.person_name == "Sara M."
    assert filled.position_name == "Lead Teacher"
    assert filled.permit_status == "permit_in_process"
    assert filled.notes == "Moved to lead slot."
    window.window.close()
    app.processEvents()

def test_pyside_mark_coming_uses_guided_dialog_and_saves_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "slots": [
                                    {
                                        "position_name": "Aide 1",
                                        "position_type": "Aide",
                                        "status": "need_now",
                                        "current_opened_date": "2026-07-01",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    assignment_id = next(row.id for row in window.staffing_store.list_assignments() if row.position_name == "Aide 1")
    monkeypatch.setattr(
        window.QtWidgets.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy prompt")),
    )

    window._mark_staffing_coming(assignment_id)
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "PySideStaffingMarkComingDialog")
    assert dialog is not None
    assert dialog.isVisible()
    assert {
        label.text() for label in dialog.findChildren(qt_widgets.QLabel) if label.text()
    } >= {
        "Mark Coming",
        "Teacher Name *",
        "Start Date *",
        "Role *",
        "Permit Status *",
        "Units",
    }
    dialog.findChild(qt_widgets.QLineEdit, "PySideStaffingComingName").setText("Samantha Lee")
    dialog.findChild(qt_widgets.QLineEdit, "PySideStaffingComingStartDate").setText("2026-08-01")
    dialog.findChild(qt_widgets.QPushButton, "PySideStaffingComingSave").click()
    app.processEvents()

    updated = window.staffing_store.get_assignment(assignment_id)
    assert updated.status == "coming"
    assert updated.person_name == "Samantha Lee"
    assert updated.start_date == "2026-08-01"
    window.window.close()
    app.processEvents()

def test_pyside_replace_employee_uses_guided_dialog_and_saves_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_test = pytest.importorskip("PySide6.QtTest")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Destiny",
                                "slots": [
                                    {
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Angie R.", "permit_status": "permit_in_process"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Palmdale"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    assignment_id = next(row.id for row in window.staffing_store.list_assignments() if row.position_name == "Teacher 2")
    monkeypatch.setattr(
        window.QtWidgets.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy prompt")),
    )

    window._mark_staffing_replacing(assignment_id)
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "PySideStaffingReplaceDialog")
    assert dialog is not None
    assert dialog.isVisible()
    assert {
        label.text() for label in dialog.findChildren(qt_widgets.QLabel) if label.text()
    } >= {"Replace Employee", "Current Employee", "Notice Given *", "Final Working Day *", "Reason (optional)"}
    notice_field = dialog.findChild(qt_widgets.QDateEdit, "PySideStaffingReplaceNotice")
    final_day_field = dialog.findChild(qt_widgets.QDateEdit, "PySideStaffingReplaceFinalDay")
    reason_field = dialog.findChild(qt_widgets.QComboBox, "PySideStaffingReplaceReason")
    assert notice_field is not None
    assert final_day_field is not None
    assert notice_field.calendarPopup()
    assert final_day_field.calendarPopup()
    assert notice_field.date() == qt_core.QDate.currentDate()
    assert final_day_field.date() == qt_core.QDate.currentDate()
    assert reason_field is not None
    assert [reason_field.itemText(index) for index in range(reason_field.count())] == [
        "Resignation",
        "Termination",
        "Leave of absence",
        "Transfer",
        "Other",
    ]
    save_button = dialog.findChild(qt_widgets.QPushButton, "PySideStaffingReplaceSave")
    qt_test.QTest.mouseClick(notice_field, qt_core.Qt.MouseButton.LeftButton, pos=notice_field.rect().center())
    app.processEvents()
    assert notice_field.calendarWidget().isVisible()
    calendar_parent = notice_field.calendarWidget().parentWidget()
    if calendar_parent is not None:
        calendar_parent.hide()
    assert window.staffing_store.get_assignment(assignment_id).status == "filled"

    notice_field.setDate(qt_core.QDate(2026, 8, 1))
    final_day_field.setDate(qt_core.QDate(2026, 8, 15))
    reason_field.setCurrentText("Transfer")
    save_button.click()
    app.processEvents()

    updated = window.staffing_store.get_assignment(assignment_id)
    assert updated.status == "replace"
    assert updated.person_name == "Angie R."
    assert updated.notice_given == "2026-08-01"
    assert updated.final_working_day == "2026-08-15"
    window.window.close()
    app.processEvents()

def test_pyside_update_permit_uses_guided_dialog_and_saves_people_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "slots": [
                                    {
                                        "position_name": "Teacher 3",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Denise A.", "permit_status": "permit_in_process"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    assignment_id = next(row.id for row in window.staffing_store.list_assignments() if row.position_name == "Teacher 3")
    monkeypatch.setattr(
        window.QtWidgets.QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy prompt")),
    )

    window._update_staffing_permit(assignment_id)
    app.processEvents()

    dialog = window.window.findChild(qt_widgets.QDialog, "PySideStaffingPermitDialog")
    assert dialog is not None
    assert dialog.isVisible()
    assert {
        label.text() for label in dialog.findChildren(qt_widgets.QLabel) if label.text()
    } >= {"Update Permit Status", "Permit Status", "Units", "Effective Date"}
    permit_combo = dialog.findChild(qt_widgets.QComboBox, "PySideStaffingPermitStatus")
    permit_combo.setCurrentText("teacher_permit_approved")
    dialog.findChild(qt_widgets.QPushButton, "PySideStaffingPermitSave").click()
    app.processEvents()

    updated = window.staffing_store.get_assignment(assignment_id)
    assert updated.permit_status == "teacher_permit_approved"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_dashboard_visual_render_uses_real_seed_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    repo_root = Path(__file__).resolve().parents[1]
    seed_path = repo_root / "config" / "staffing_seed.json"
    seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
    expected_schools = [school["name"] for school in seed_data["schools"]]
    expected_assignment_count = sum(
        len(classroom.get("slots", classroom.get("positions", [])))
        for school in seed_data["schools"]
        for classroom in school.get("classrooms", [])
    ) + sum(
        len(row.get("slots", row.get("positions", [])))
        for school in seed_data["schools"]
        for row in school.get("support_rows", [])
    )
    db_path = tmp_path / "staffing.sqlite3"
    partial_store = pyside_interview_app.StaffingStore(db_path)
    partial_store.initialize()
    partial_store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
        status="filled",
        person_name="Angie",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", db_path)
    monkeypatch.chdir(repo_root / "src")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )

    window = _pyside_window_on_page(model, "Staffing")
    window.window.resize(1600, 1100)
    window.window.show()
    app.processEvents()
    tabs = window.window.findChild(qt_widgets.QTabWidget, "PySideStaffingSchoolTabs")
    selector = window.window.findChild(qt_widgets.QComboBox, "PySideStaffingSchoolSelector")
    labels = [label.text() for label in window.stack.widget(3).findChildren(qt_widgets.QLabel)]
    rendered = window.stack.widget(3).grab()
    screenshot_path = tmp_path / "staffing_dashboard_visual.png"
    assert rendered.save(str(screenshot_path))

    assert [tabs.tabText(index) for index in range(tabs.count())] == expected_schools
    assert [selector.itemText(index) for index in range(selector.count())] == expected_schools
    assert len(partial_store.list_assignments()) == expected_assignment_count
    assert "Color Code Key" not in labels
    assert rendered.width() >= 600
    assert rendered.height() >= 400
    image = rendered.toImage()
    sample_points = [
        image.pixelColor(x, y).name()
        for x in range(0, image.width(), max(1, image.width() // 8))
        for y in range(0, image.height(), max(1, image.height() // 8))
    ]
    assert len(set(sample_points)) > 3
    window.window.close()
    app.processEvents()

def test_pyside_staffing_action_surfaces_exact_service_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", tmp_path / "missing.json")
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")

    window._run_staffing_action(lambda service: service.open_position(999), "unused")

    assert "Assignment not found." in window.staffing_status_label.text()
    window.window.close()
    app.processEvents()

def test_pyside_staffing_actions_use_notification_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "dont_need_now"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    notifications = []

    class FakeNotifications:
        def emit_event(self, event_type, payload, idempotency_key):
            notifications.append((event_type, payload, idempotency_key))
            return []

    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    monkeypatch.setattr(
        pyside_interview_app,
        "notification_service_from_email_account_settings",
        lambda **_kwargs: FakeNotifications(),
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    table = window.window.findChild(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")

    _staffing_button_for_position(table, "Teacher 1").click()
    app.processEvents()

    assert notifications
    assert notifications[0][0] == "staffing.assignment.need_now"
    window.window.close()
    app.processEvents()

def test_pyside_staffing_uses_single_draggable_colored_workbook_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    qt_core = pytest.importorskip("PySide6.QtCore")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {"name": "Hawthorne", "classrooms": [{"name": "Tranquility", "positions": [{"position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Angie"}}]}]},
                    {"name": "Palmdale", "classrooms": [{"name": "Harmony", "positions": [{"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"}]}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne", "Palmdale"],
    )

    window = _pyside_window_on_page(model, "Staffing")
    tabs = window.window.findChild(qt_widgets.QTabWidget, "PySideStaffingSchoolTabs")
    workbook_tables = window.window.findChildren(qt_widgets.QTableWidget, "PySideStaffingWorkbookBoard")
    flat_tables = window.window.findChildren(qt_widgets.QTableWidget, "PySideStaffingAssignments")

    assert len(workbook_tables) == 2
    assert flat_tables == []
    assert all(table.dragEnabled() for table in workbook_tables)
    assert all(table.acceptDrops() for table in workbook_tables)
    assert workbook_tables[0].dragDropMode() == qt_widgets.QAbstractItemView.DragDropMode.DragDrop
    assert [
        workbook_tables[0].horizontalHeaderItem(index).text()
        for index in range(workbook_tables[0].columnCount())
    ] == ["Ratio", "Classroom", "Person", "Status", "Capacity", "Permit Status", "Details", "Action"]
    assert isinstance(workbook_tables[0].cellWidget(_staffing_row_for_position(workbook_tables[0], "Teacher 1"), 5), qt_widgets.QComboBox)
    assert tabs.tabBar().tabTextColor(0) != tabs.tabBar().tabTextColor(1)
    assert tabs.tabBar().tabTextColor(0).isValid()
    assert workbook_tables[0].item(_staffing_row_for_position(workbook_tables[0], "Teacher 1"), 4).flags() & qt_core.Qt.ItemFlag.ItemIsDragEnabled
    window.window.close()
    app.processEvents()

def test_pyside_staffing_confirm_move_updates_source_and_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Angie"}},
                                    {"position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now"},
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "interview_history.sqlite3",
        school_options=["Hawthorne"],
    )
    window = _pyside_window_on_page(model, "Staffing")
    yes = window.QtWidgets.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(window.QtWidgets.QMessageBox, "question", lambda *_args, **_kwargs: yes)
    assignments = window.staffing_store.list_assignments()
    source_id = next(row.id for row in assignments if row.position_name == "Teacher 1")
    target_id = next(row.id for row in assignments if row.position_name == "Teacher 2")

    assert window._confirm_staffing_move(source_id, target_id) is True

    source = window.staffing_store.get_assignment(source_id)
    target = window.staffing_store.get_assignment(target_id)
    assert source.status == "need_now"
    assert source.person_name == ""
    assert target.status == "filled"
    assert target.person_name == "Angie"
    window.window.close()
    app.processEvents()

def test_staffing_v2_director_interviews_sync_pending_history_and_record_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Harmony 1",
                                "slots": [
                                    {"position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now"}
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Palmdale",
                        "classrooms": [
                            {
                                "name": "Destiny",
                                "slots": [
                                    {"position_name": "Teacher 1", "position_type": "Teacher", "status": "need_now"}
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    history_path = tmp_path / "interview_history.sqlite3"
    history_store = InterviewHistoryStore(history_path)
    history_store.append(
        {
            "history_id": "hist-old-hawthorne",
            "candidate_name": "Past Candidate",
            "candidate_email": "past@example.org",
            "school": "Hawthorne",
            "position": "Teacher",
            "interview_date": "2026-07-05",
            "outcome": "Hire",
            "score": "8.5",
        }
    )
    history_store.append(
        {
            "history_id": "hist-palmdale",
            "candidate_name": "Riley Park",
            "school": "Palmdale",
            "position": "Teacher",
            "interview_date": "2026-07-05",
            "outcome": "Borderline",
            "score": "7.5",
        }
    )
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", tmp_path / "staffing.sqlite3")
    monkeypatch.setattr(pyside_interview_app, "STAFFING_SEED_PATH", seed_path)
    store = pyside_interview_app.StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    pyside_interview_app.StaffingService(store).upsert_director_candidate_referral(
        history_id="new-hawthorne",
        candidate_name="Jordan Lee",
        school="Hawthorne",
        position="Teacher",
        interviewer_rating=8.5,
        interviewer_outcome="hire",
        interview_date="2026-07-07",
        candidate_email="jordan@example.org",
    )
    model = pyside_interview_app.build_director_staffing_model(
        build_interview_redesign_model(
            rubric_path=_write_test_rubric(tmp_path),
            overrides_path=_write_test_overrides(tmp_path),
            history_path=history_path,
            school_options=["Hawthorne", "Palmdale"],
        ),
        school="Hawthorne",
    )

    window = pyside_interview_app.PySideInterviewWindow(model)
    panel = window.window.findChild(qt_widgets.QFrame, "StaffingV2DirectorInterviewPanel")
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")

    assert panel is not None
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Jordan Lee"
    rendered_candidates = {
        table.item(row, 0).text()
        for row in range(table.rowCount())
        if table.item(row, 0) is not None
    }
    assert "Past Candidate" not in rendered_candidates
    assert "Riley Park" not in rendered_candidates

    table.cellWidget(0, table.columnCount() - 1).click()
    app.processEvents()
    dialog = window.window.findChild(qt_widgets.QDialog, "StaffingV2DirectorInterviewDialog")
    assert dialog is not None
    dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewDirectorName").setText("Avery Director")
    dialog.findChild(qt_widgets.QDoubleSpinBox, "StaffingV2DirectorInterviewRating").setValue(9.0)
    decision = dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewDecision")
    shift_start = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewShiftStartText")
    shift_end = dialog.findChild(qt_widgets.QLineEdit, "StaffingV2DirectorInterviewShiftEndText")
    classroom = dialog.findChild(qt_widgets.QComboBox, "StaffingV2DirectorInterviewClassroom")
    decision.setCurrentText("No-Hire")
    app.processEvents()
    assert not shift_start.isVisible()
    assert not shift_end.isVisible()
    assert not classroom.isVisible()
    decision.setCurrentText("Hire")
    app.processEvents()
    assert shift_start.isVisible()
    assert shift_end.isVisible()
    assert classroom.isVisible()
    shift_start.setText("8:00 AM")
    shift_end.setText("5:00 PM")
    classroom.setCurrentText("Harmony 1")
    dialog.findChild(qt_widgets.QTextEdit, "StaffingV2DirectorInterviewNotes").setPlainText("Strong classroom presence.")
    dialog.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewSave").click()
    app.processEvents()

    assert table.rowCount() == 0
    history_table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewHistoryTable")
    assert history_table.rowCount() == 1
    assert history_table.item(0, 0).text() == "Jordan Lee"
    assignment = next(row for row in window.staffing_store.list_assignments() if row.school == "Hawthorne")
    assert assignment.status == "need_now"
    assert assignment.person_name == ""
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_staffing_v2_director_interviews_backfill_passed_history_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    history_store = InterviewHistoryStore(history_path)
    history_store.append(
        {
            "history_id": "hist-hire",
            "candidate_name": "Hire Candidate",
            "candidate_email": "hire@example.org",
            "school": "Palmdale",
            "position": "Teacher",
            "interview_date": "2026-07-08",
            "outcome": "Hire",
            "score": "88%",
        }
    )
    history_store.append(
        {
            "history_id": "hist-borderline",
            "candidate_name": "Borderline Candidate",
            "school": "Palmdale",
            "position": "Assistant Teacher",
            "interview_date": "2026-07-08",
            "outcome": "Borderline",
            "score": "72%",
        }
    )
    history_store.append(
        {
            "history_id": "hist-no-hire",
            "candidate_name": "No Hire Candidate",
            "school": "Palmdale",
            "position": "Teacher",
            "interview_date": "2026-07-08",
            "outcome": "No Hire",
            "score": "42%",
        }
    )
    history_store.append(
        {
            "history_id": "hist-other-school",
            "candidate_name": "Other School Candidate",
            "school": "Hawthorne",
            "position": "Teacher",
            "interview_date": "2026-07-08",
            "outcome": "Hire",
            "score": "90%",
        }
    )
    staffing_path = tmp_path / "staffing.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", staffing_path)
    store = pyside_interview_app.StaffingStore(
        pyside_interview_app.staffing_db_path_for_school("Palmdale", base_path=staffing_path)
    )
    store.initialize()
    model = pyside_interview_app.build_director_staffing_model(
        build_interview_redesign_model(
            rubric_path=_write_test_rubric(tmp_path),
            overrides_path=_write_test_overrides(tmp_path),
            history_path=history_path,
            school_options=["Palmdale", "Hawthorne"],
        ),
        school="Palmdale",
    )

    window = pyside_interview_app.PySideInterviewWindow(model)
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")
    delete_selected = window.window.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewDeleteSelected")

    rendered_candidates = [
        table.item(row, 0).text()
        for row in range(table.rowCount())
        if table.item(row, 0) is not None
    ]
    assert rendered_candidates == ["Borderline Candidate", "Hire Candidate"]
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )
    selection_model = table.selectionModel()
    for row in range(table.rowCount()):
        selection_model.select(
            table.model().index(row, 0),
            qt_core.QItemSelectionModel.SelectionFlag.Select | qt_core.QItemSelectionModel.SelectionFlag.Rows,
        )
    delete_selected.click()
    app.processEvents()

    assert table.rowCount() == 0
    dismissed_history_ids = store.list_dismissed_director_referral_history_ids()
    assert {"hist-borderline", "hist-hire"} <= dismissed_history_ids
    assert "hist-no-hire" not in dismissed_history_ids
    assert "hist-other-school" not in dismissed_history_ids
    assert len(InterviewHistoryStore(history_path).load()) == 4
    window.window.close()
    app.processEvents()


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_director_staffing_launch_queues_history_backfill_when_edit_lock_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    history_store = InterviewHistoryStore(history_path)
    history_store.append(
        {
            "history_id": "hist-locked",
            "candidate_name": "Locked Candidate",
            "school": "Palmdale",
            "position": "Teacher",
            "interview_date": "2026-07-08",
            "outcome": "Hire",
            "score": "88%",
        }
    )
    staffing_path = tmp_path / "staffing.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", staffing_path)
    school_staffing_path = pyside_interview_app.staffing_db_path_for_school("Palmdale", base_path=staffing_path)
    store = pyside_interview_app.StaffingStore(school_staffing_path)
    store.initialize()
    lock_path = school_staffing_path.with_suffix(school_staffing_path.suffix + ".editing.lock")
    lock_path.write_text(
        json.dumps(
            {
                "owner": "other-director",
                "created_at": "2099-01-01T00:00:00Z",
                "database": str(school_staffing_path),
            }
        ),
        encoding="utf-8",
    )
    model = pyside_interview_app.build_director_staffing_model(
        build_interview_redesign_model(
            rubric_path=_write_test_rubric(tmp_path),
            overrides_path=_write_test_overrides(tmp_path),
            history_path=history_path,
            school_options=["Palmdale"],
        ),
        school="Palmdale",
    )

    window = pyside_interview_app.PySideInterviewWindow(model)
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")

    assert table is not None
    assert table.rowCount() == 0
    window.window.close()
    app.processEvents()
    assert store.pending_operations_path.exists()
    lock_path.unlink()
    assert pyside_interview_app.StaffingService(store).flush_pending_operations() == 1
    pending = store.list_director_candidate_referrals(school="Palmdale")
    assert [candidate.candidate_name for candidate in pending] == ["Locked Candidate"]


@pytest.mark.pyside_gui
@pytest.mark.slow_pyside
def test_staffing_v2_director_interviews_delete_checked_pending_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.sqlite3"
    staffing_path = tmp_path / "staffing.sqlite3"
    monkeypatch.setattr(pyside_interview_app, "STAFFING_DB_PATH", staffing_path)
    store = pyside_interview_app.StaffingStore(
        pyside_interview_app.staffing_db_path_for_school("Palmdale", base_path=staffing_path)
    )
    store.initialize()
    service = pyside_interview_app.StaffingService(store)
    for history_id, candidate_name in [
        ("hist-delete-1", "Adrianna Love"),
        ("hist-delete-2", "Michelle Marti"),
        ("hist-keep", "Catherine Gray"),
    ]:
        service.upsert_director_candidate_referral(
            history_id=history_id,
            candidate_name=candidate_name,
            school="Palmdale",
            position="Teacher",
            interviewer_rating=8.0,
            interviewer_outcome="hire",
            interview_date="2026-07-08",
        )
    model = pyside_interview_app.build_director_staffing_model(
        build_interview_redesign_model(
            rubric_path=_write_test_rubric(tmp_path),
            overrides_path=_write_test_overrides(tmp_path),
            history_path=history_path,
            school_options=["Palmdale"],
        ),
        school="Palmdale",
    )

    window = pyside_interview_app.PySideInterviewWindow(model)
    table = window.window.findChild(qt_widgets.QTableWidget, "StaffingV2DirectorInterviewPendingTable")
    delete_selected = window.window.findChild(qt_widgets.QPushButton, "StaffingV2DirectorInterviewDeleteSelected")

    rendered_rows = {
        table.item(row, 0).text(): row
        for row in range(table.rowCount())
        if table.item(row, 0) is not None
    }
    assert table.selectionMode() == qt_widgets.QAbstractItemView.SelectionMode.MultiSelection
    assert set(rendered_rows) == {"Adrianna Love", "Michelle Marti", "Catherine Gray"}
    adrianna_checkbox = table.cellWidget(rendered_rows["Adrianna Love"], 0)
    michelle_checkbox = table.cellWidget(rendered_rows["Michelle Marti"], 0)
    assert adrianna_checkbox is not None
    assert michelle_checkbox is not None
    assert adrianna_checkbox.text() == "Adrianna Love"
    assert michelle_checkbox.text() == "Michelle Marti"
    adrianna_checkbox.setChecked(True)
    michelle_checkbox.setChecked(True)
    monkeypatch.setattr(
        qt_widgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: qt_widgets.QMessageBox.StandardButton.Yes,
    )
    delete_selected.click()
    app.processEvents()

    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "Catherine Gray"
    assert [candidate.candidate_name for candidate in service.list_pending_director_interviews(school="Palmdale")] == [
        "Catherine Gray"
    ]
    window.window.close()
    app.processEvents()


def _staffing_row_for_position(table, position_name: str) -> int:
    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is not None and item.toolTip() == position_name:
                return row
    raise AssertionError(f"Missing staffing row: {position_name}")


def _staffing_button_for_position(table, position_name: str):
    row = _staffing_row_for_position(table, position_name)
    button = table.cellWidget(row, table.columnCount() - 1)
    assert button is not None
    return button


def _write_test_overrides(tmp_path: Path) -> Path:
    overrides_path = tmp_path / "question_overrides.json"
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {
                    "preschool": [{"id": "Why-LPL", "text": "Why Launch Pad Learning?", "order": 1}]
                },
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "trait", "id": "trait_1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return overrides_path


def _write_workflow_test_overrides(tmp_path: Path) -> Path:
    overrides_path = tmp_path / "question_overrides.json"
    overrides_path.write_text(
        json.dumps(
            {
                "track_trait_order": {},
                "trait_question_overrides": {},
                "custom_questions": {
                    "preschool": [
                        {"id": "Why-ECE", "text": "Why early childhood education?", "order": 1},
                        {"id": "Why-LPL", "text": "Why Launch Pad Learning?", "order": 2},
                        {"id": "FT-or-PT", "text": "Full-time or part-time?", "order": 3},
                        {"id": "Not-Avail", "text": "Any hours unavailable?", "order": 4},
                        {"id": "Pay", "text": "What pay are you looking for?", "order": 5},
                        {"id": "Start", "text": "When could you start?", "order": 6},
                    ]
                },
                "track_question_flow": {
                    "preschool": [
                        {"type": "custom", "id": "Why-ECE"},
                        {"type": "custom", "id": "Why-LPL"},
                        {"type": "trait", "id": "trait_1"},
                        {"type": "custom", "id": "FT-or-PT"},
                        {"type": "custom", "id": "Not-Avail"},
                        {"type": "custom", "id": "Pay"},
                        {"type": "custom", "id": "Start"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return overrides_path
