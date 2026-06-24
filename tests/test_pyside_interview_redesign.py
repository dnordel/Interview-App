import json
import os
from datetime import date
from pathlib import Path

import pytest
import pyside_interview_app
from data_store import InterviewHistoryStore
from docx import Document

from onboarding_operations import Employee, EmployeeTask
from pyside_interview_app import (
    PySideInterviewSession,
    build_interview_redesign_model,
    build_pyside_admin_studio_model,
    build_pyside_candidate_board,
    build_pyside_onboarding_board,
    latest_pyside_draft_path,
    standard_window_control_flags,
)


def test_redesign_model_prioritizes_guided_interview_workflow(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    overrides_path = tmp_path / "question_overrides.json"
    history_path = tmp_path / "interview_history.json"

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
    history_path.write_text(
        json.dumps(
            [
                {
                    "candidate_name": "Latoya Nugent",
                    "school": "Palmdale",
                    "track": "Preschool",
                    "score": 60.95,
                    "status": "Finalized",
                    "next_action": "Generate Offer",
                }
            ]
        ),
        encoding="utf-8",
    )

    model = build_interview_redesign_model(
        rubric_path=rubric_path,
        overrides_path=overrides_path,
        history_path=history_path,
        school_options=["Palmdale"],
    )

    assert model.app_title == "Interview Assistant"
    assert model.navigation == ["Interviews", "Candidates", "Offers", "Onboarding", "Admin"]
    assert model.home.primary_action == "Start a New Interview"
    assert model.home.admin_visible_on_home is False
    assert model.home.recent_interviews[0].next_action == "Generate Offer"
    assert model.setup_steps == ["Candidate", "Interview Plan", "Ready"]

    preschool_flow = model.flows["preschool"]
    assert [item.kind for item in preschool_flow.items] == ["custom", "trait"]
    assert preschool_flow.items[0].prompt == "Why Launch Pad Learning?"
    scored = preschool_flow.items[1]
    assert scored.score_cards[0].label == "1"
    assert scored.score_cards[0].description == "Serious concern"
    assert "Needs follow-up" in scored.quick_actions
    assert "Disqualifier observed" in scored.quick_actions


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
    history_path = tmp_path / "interview_history.json"
    history_path.write_text(
        json.dumps(
            [
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
            ]
        ),
        encoding="utf-8",
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


def test_pyside_history_grid_shows_date_and_open_notes_action(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx placeholder", encoding="utf-8")
    history_path = tmp_path / "interview_history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "hist-1",
                    "candidate_name": "Latoya Nugent",
                    "school": "Palmdale",
                    "position": "Preschool Teacher",
                    "interview_date": "2026-06-23",
                    "offer_status": "not_generated",
                    "interview_notes_path": str(notes_path),
                }
            ]
        ),
        encoding="utf-8",
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model)

    assert window.history_table.columnCount() == 8
    assert window.history_table.horizontalHeaderItem(0).text() == "Date"
    assert window.history_table.horizontalHeaderItem(6).text() == "Interview Notes"
    assert window.history_table.item(0, 0).text() == "2026-06-23"
    notes_button = window.history_table.cellWidget(0, 6)
    assert notes_button.text() == "Open Notes"
    assert notes_button.property("history_row_key") == "hist-1"
    assert notes_button.isEnabled()
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
    assert "Final Outcome: Hire" in rendered


def test_pyside_finalize_uses_desktop_artifacts_history_and_deepseek_queue(tmp_path: Path, monkeypatch) -> None:
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
    history_path = tmp_path / "interview_history.json"
    queued_jobs: list[tuple[str, str]] = []

    def _fake_enqueue(_app, _context, out_path: str, history_id: str) -> Path:
        queued_jobs.append((str(out_path), history_id))
        job_path = tmp_path / "deepseek_jobs" / f"deepseek-finalize-{history_id}.json"
        job_path.parent.mkdir(parents=True, exist_ok=True)
        job_path.write_text("{}", encoding="utf-8")
        return job_path

    monkeypatch.setattr(pyside_interview_app, "enqueue_deepseek_finalize_job", _fake_enqueue)

    result = session.finalize_interview(base_dir=tmp_path, history_path=history_path)

    report_path = Path(result["out_path"])
    integration_path = Path(result["integration_path"])
    rows = InterviewHistoryStore(history_path).load()

    assert report_path.exists()
    assert integration_path.exists()
    assert "pyside_notes" not in str(report_path)
    assert rows[0]["candidate_name"] == "Latoya Nugent"
    assert Path(rows[0]["interview_notes_path"]) == report_path
    assert rows[0]["deepseek_processing_status"] == "processing"
    assert queued_jobs == [(str(report_path), rows[0]["history_id"])]
    assert result["deepseek_job_path"].endswith(f"deepseek-finalize-{rows[0]['history_id']}.json")


def test_pyside_review_source_exposes_finalize_button_not_placeholder_notes() -> None:
    source = Path("src/pyside_interview_app.py").read_text(encoding="utf-8")

    assert 'self._primary_button("Finalize Interview")' in source
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


def test_pyside_admin_studio_model_separates_advanced_config(tmp_path: Path) -> None:
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=tmp_path / "missing-history.json",
        school_options=["Palmdale"],
    )

    admin = build_pyside_admin_studio_model(model)

    assert admin.sections == ["Role Tracks", "Questions", "Rubrics", "Signals", "Templates", "Storage", "Security"]
    assert admin.track_count == 1
    assert admin.question_count == 2
    assert admin.advanced_json_hidden is True
    assert "Review validation warnings before saving rubric or scoring changes." in admin.validation_warnings


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


def test_pyside_history_generate_offer_button_prefills_offer_wizard(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    history_path = tmp_path / "interview_history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "hist-1",
                    "candidate_name": "Latoya Nugent",
                    "school": "Palmdale",
                    "position": "Preschool Teacher",
                    "outcome": "Hire",
                    "offer_status": "not_generated",
                }
            ]
        ),
        encoding="utf-8",
    )
    model = build_interview_redesign_model(
        rubric_path=_write_test_rubric(tmp_path),
        overrides_path=_write_test_overrides(tmp_path),
        history_path=history_path,
        school_options=["Palmdale"],
    )
    window = pyside_interview_app.PySideInterviewWindow(model)

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
    history_path = tmp_path / "interview_history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "hist-1",
                    "candidate_name": "Latoya Nugent",
                    "school": "Palmdale",
                    "position": "Preschool Teacher",
                    "outcome": "Hire",
                    "offer_status": "not_generated",
                }
            ]
        ),
        encoding="utf-8",
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
    window = pyside_interview_app.PySideInterviewWindow(model)
    window._open_history_offer(model.home.history_rows[0])
    window.offer_fields["template_path"].setText(str(template_path))
    window.offer_fields["output_dir"].setText(str(tmp_path / "offers"))
    window.offer_fields["start_date"].setText("2026-06-23")
    window.offer_fields["hourly_pay"].setText("22.50")
    window.offer_fields["hours_week"].setText("40")

    window._generate_offer_from_fields()

    rows = InterviewHistoryStore(history_path).load()
    assert rows[0]["offer_status"] == "generated"
    assert rows[0]["offer_letter_path"].endswith("2026-06-24 - Offer - Latoya_Nugent.docx")
    assert "Offer generated:" in window.offer_status_label.text()
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
    window = pyside_interview_app.PySideInterviewWindow(model)

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
