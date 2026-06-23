import json
from datetime import date
from pathlib import Path

from docx import Document

from onboarding_operations import Employee, EmployeeTask
from pyside_interview_app import (
    PySideInterviewSession,
    build_interview_redesign_model,
    build_pyside_admin_studio_model,
    build_pyside_candidate_board,
    build_pyside_onboarding_board,
    latest_pyside_draft_path,
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
    assert resumed.active_question().kind == "trait"


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
    rendered = "\n".join(paragraph.text for paragraph in Document(output_path).paragraphs)
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
    session.save_answer_and_advance(notes="Warm child-centered example.", score="5")

    output_path = session.generate_interview_notes_document(output_dir=tmp_path / "notes")

    assert output_path.exists()
    rendered = "\n".join(paragraph.text for paragraph in Document(output_path).paragraphs)
    assert "Latoya Nugent" in rendered
    assert "Warm child-centered example." in rendered
    assert "Determination: Hire" in rendered


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
