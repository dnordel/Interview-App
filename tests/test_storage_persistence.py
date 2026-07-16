import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from data_store import (
    InterviewHistoryStore,
    QuestionOverridesStore,
    SchoolEmailTemplateStore,
    SchoolOfferSettingsStore,
    default_school_offer_settings,
    resolve_interview_notes_output_dir,
    resolve_offer_output_dir,
    resolve_offer_template_path,
)
from onboarding_operations import DEFAULT_EXPECTED_INTERVAL_HOURS
from onboarding_operations import scheduler_expected_interval_hours, scheduler_opt_in
import onboarding_operations
from onboarding_operations import JsonStore
from onboarding_operations import build_onboarding_overview
from storage_utils import safe_read_json


def test_question_overrides_store_save_persists_same_shape(tmp_path: Path):
    path = tmp_path / "question_overrides.json"
    store = QuestionOverridesStore(path)
    store.data = {
        "track_trait_order": {"lead": ["trait_3", "trait_1"]},
        "trait_question_overrides": {"trait_1": "Tell me about classroom management."},
        "custom_questions": {
            "lead": [{"id": "cq_1", "text": "Custom?", "order": 1}],
        },
        "track_question_flow": {
            "lead": [{"type": "custom", "id": "cq_1"}],
        },
    }

    store.save()

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == store.data


def test_interview_history_store_append_persists_list(tmp_path: Path):
    path = tmp_path / "interview_history.json"
    store = InterviewHistoryStore(path)

    store.append({"candidate": "A", "score": 90})
    store.append({"candidate": "B", "score": 88})

    assert store.load() == [
        {"candidate": "A", "score": 90},
        {"candidate": "B", "score": 88},
    ]
    assert store.db_path == tmp_path / "interview_history.sqlite3"
    assert store.db_path.exists()


def test_interview_history_store_imports_existing_json_once_then_uses_sqlite(tmp_path: Path):
    path = tmp_path / "interview_history.json"
    path.write_text(json.dumps([{"history_id": "old", "candidate_name": "Old Candidate"}]), encoding="utf-8")
    store = InterviewHistoryStore(path)

    assert store.load() == [{"history_id": "old", "candidate_name": "Old Candidate"}]
    store.append({"history_id": "new", "candidate_name": "New Candidate"})

    assert [row["history_id"] for row in store.load()] == ["old", "new"]
    assert json.loads(path.read_text(encoding="utf-8")) == [{"history_id": "old", "candidate_name": "Old Candidate"}]


def test_interview_history_store_persists_queryable_columns(tmp_path: Path):
    store = InterviewHistoryStore(tmp_path / "interview_history.sqlite3")

    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "candidate_email": "latoya@example.com",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "interview_date": "2026-03-11",
            "outcome": "Hire",
            "percent_of_max": 88.5,
            "offer_status": "not_generated",
        }
    )
    store.update_offer_state("hist-1", "offer_sent", "offer.docx")

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT row_key, history_id, candidate_name, candidate_email, school, position,
                   interview_date, outcome, score, offer_status
            FROM interview_history
            WHERE row_key = ?
            """,
            ("hist-1",),
        ).fetchone()

    assert dict(row) == {
        "row_key": "hist-1",
        "history_id": "hist-1",
        "candidate_name": "Latoya Nugent",
        "candidate_email": "latoya@example.com",
        "school": "Palmdale",
        "position": "Preschool Teacher",
        "interview_date": "2026-03-11",
        "outcome": "Hire",
        "score": 88.5,
        "offer_status": "offer_sent",
    }


def test_interview_history_store_adds_queryable_columns_to_existing_db(tmp_path: Path):
    db_path = tmp_path / "interview_history.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE interview_history (
                row_key TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO interview_history (row_key, sort_order, payload_json) VALUES (?, ?, ?)",
            (
                "hist-old",
                0,
                json.dumps(
                    {
                        "history_id": "hist-old",
                        "candidate_name": "Existing Candidate",
                        "school": "Hawthorne",
                        "percent_of_max": 77,
                    }
                ),
            ),
        )
        conn.commit()

    store = InterviewHistoryStore(db_path)
    assert store.load()[0]["candidate_name"] == "Existing Candidate"
    store.update_row("hist-old", {"offer_status": "generated"})

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT candidate_name, school, score, offer_status FROM interview_history WHERE row_key = ?",
            ("hist-old",),
        ).fetchone()

    assert dict(row) == {
        "candidate_name": "Existing Candidate",
        "school": "Hawthorne",
        "score": 77.0,
        "offer_status": "generated",
    }


def test_interview_history_store_row_updates_preserve_existing_created_at(tmp_path: Path):
    store = InterviewHistoryStore(tmp_path / "interview_history.sqlite3")
    store.append({"history_id": "keep-created", "candidate_name": "A", "offer_status": "not_generated"})

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE interview_history SET created_at = ? WHERE row_key = ?",
            ("2026-01-01 00:00:00", "keep-created"),
        )
        conn.commit()

    store.update_offer_state("keep-created", "offer_sent")
    store.append({"history_id": "new-row", "candidate_name": "B"})

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT row_key, created_at FROM interview_history ORDER BY sort_order"
        ).fetchall()

    assert rows == [
        ("keep-created", "2026-01-01 00:00:00"),
        ("new-row", rows[1][1]),
    ]


def test_interview_history_store_load_filtered_uses_query_columns(tmp_path: Path):
    store = InterviewHistoryStore(tmp_path / "interview_history.sqlite3")
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Latoya Nugent",
            "school": "Palmdale",
            "position": "Preschool Teacher",
            "outcome": "Hire",
            "offer_status": "offer_sent",
        }
    )
    store.append(
        {
            "history_id": "hist-2",
            "candidate_name": "Dana Teacher",
            "school": "Hawthorne",
            "position": "Infant Teacher",
            "outcome": "Borderline",
            "offer_status": "not_generated",
        }
    )
    store.append(
        {
            "history_id": "hist-3",
            "candidate_name": "Morgan Lead",
            "school": "Palmdale",
            "position": "Lead Teacher",
            "outcome": "No Hire",
            "offer_status": "not_generated",
            "interview_notes_path": str(tmp_path / "Morgan_Lead_notes.docx"),
        }
    )

    assert [row["history_id"] for row in store.load_filtered(school="Palmdale")] == ["hist-1", "hist-3"]
    assert [row["history_id"] for row in store.load_filtered(outcome="hire")] == ["hist-1"]
    assert [row["history_id"] for row in store.load_filtered(offer_status="not_generated", limit=1)] == ["hist-2"]
    assert [row["history_id"] for row in store.load_filtered(search="lead")] == ["hist-3"]
    assert [row["history_id"] for row in store.load_filtered(search="notes.docx")] == ["hist-3"]


def test_interview_history_store_loads_legacy_root_history(tmp_path: Path):
    canonical_dir = tmp_path / "user_artifacts"
    canonical_path = canonical_dir / "interview_history.json"
    legacy_path = tmp_path / "interview_history.json"
    legacy_path.write_text(
        json.dumps([{"history_id": "old-1", "candidate_name": "Legacy Candidate"}]),
        encoding="utf-8",
    )
    store = InterviewHistoryStore(canonical_path)

    assert store.load() == [{"history_id": "old-1", "candidate_name": "Legacy Candidate"}]


def test_interview_history_store_update_row_by_stable_key(tmp_path: Path):
    path = tmp_path / "interview_history.json"
    store = InterviewHistoryStore(path)

    row = {
        "history_id": "row-123",
        "candidate_name": "A",
        "interview_date": "2026-02-20",
        "saved_at": "2026-02-20T10:00:00Z",
        "offer_status": "not_generated",
    }
    store.append(row)

    assert store.update_row("row-123", {"offer_status": "generated"}) is True
    assert store.load()[0]["offer_status"] == "generated"


def test_interview_history_store_delete_row_by_stable_key(tmp_path: Path):
    path = tmp_path / "interview_history.json"
    store = InterviewHistoryStore(path)
    store.append({"history_id": "keep", "candidate_name": "A"})
    store.append({"history_id": "delete", "candidate_name": "B"})

    assert store.delete_row("delete") is True
    assert store.load() == [{"history_id": "keep", "candidate_name": "A"}]
    assert store.delete_row("missing") is False


def test_interview_history_store_repairs_missing_interview_notes_links(tmp_path: Path):
    history_path = tmp_path / "interview_history.json"
    notes_dir = tmp_path / "interviews" / "Indeed Interview Notes"
    notes_dir.mkdir(parents=True)
    notes_path = notes_dir / "2026-03-11 - Palmdale - Carolina Garcia - Interview.docx"
    notes_path.write_text("docx placeholder", encoding="utf-8")
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Carolina Garcia",
            "school": "Palmdale",
            "interview_date": "2026-03-11",
        }
    )

    assert store.repair_interview_notes_links(notes_dir) == 1

    row = store.load()[0]
    assert row["interview_notes_path"] == str(notes_path)
    assert row["saved_report_path"] == str(notes_path)
    assert row["notes_path"] == str(notes_path)
    assert row["report_path"] == str(notes_path)


def test_interview_history_store_does_not_replace_existing_valid_notes_link(tmp_path: Path):
    history_path = tmp_path / "interview_history.json"
    notes_dir = tmp_path / "interviews" / "Indeed Interview Notes"
    notes_dir.mkdir(parents=True)
    existing_path = notes_dir / "existing.docx"
    existing_path.write_text("existing", encoding="utf-8")
    (notes_dir / "2026-03-11 - Palmdale - Carolina Garcia - Interview.docx").write_text(
        "new",
        encoding="utf-8",
    )
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Carolina Garcia",
            "school": "Palmdale",
            "interview_date": "2026-03-11",
            "interview_notes_path": str(existing_path),
        }
    )

    assert store.repair_interview_notes_links(notes_dir) == 0
    assert store.load()[0]["interview_notes_path"] == str(existing_path)

def test_school_offer_settings_store_save_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "school_offer_settings.json"
    store = SchoolOfferSettingsStore(path)

    payload = {
        "north-campus": {
            "full_time_template": "full.docx",
            "part_time_template": "part.docx",
            "contractor_template": "contractor.docx",
            "offer_output_dir": "offers",
            "interview_notes_dir": "notes",
        }
    }
    store.save(payload)

    assert store.load() == payload


def test_default_school_offer_settings_include_school_offer_paths() -> None:
    settings = default_school_offer_settings()

    assert settings["Palmdale"]["full_time_template"] == (
        r"\Dropbox\HR-PMD\PMD Employment Offers\.Launch Pad Learning PMD Offer of Employment TEMPLATE - FULL TIME.docx"
    )
    assert settings["North Long Beach"]["part_time_template"] == (
        r"\Dropbox\HR-NLB\NLB Employment Offers\.Launch Pad Learning NLB Offer of Employment TEMPLATE - PART TIME.docx"
    )
    assert settings["Hawthorne"]["offer_output_dir"] == r"\Dropbox\HR-HAW\HAW Employment Offers"


@pytest.mark.parametrize(
    ("hours", "expected_template"),
    [(29, "part.docx"), (30, "full.docx")],
)
def test_offer_path_resolution_selects_template_from_weekly_hours(
    tmp_path: Path,
    hours: int,
    expected_template: str,
) -> None:
    dropbox_root = tmp_path / "Dropbox (Test)"
    base_dir = dropbox_root / "App" / "user_artifacts"
    settings = {
        "Palmdale": {
            "full_time_template": r"\Dropbox\HR-PMD\PMD Employment Offers\full.docx",
            "part_time_template": r"\Dropbox\HR-PMD\PMD Employment Offers\part.docx",
            "offer_output_dir": r"\Dropbox\HR-PMD\PMD Employment Offers",
        }
    }

    template = resolve_offer_template_path(base_dir, "Palmdale", hours, settings)
    output = resolve_offer_output_dir(base_dir, "Palmdale", settings)

    assert template == dropbox_root / "HR-PMD" / "PMD Employment Offers" / expected_template
    assert output == dropbox_root / "HR-PMD" / "PMD Employment Offers"


def test_resolve_interview_notes_output_dir_uses_school_setting_under_current_dropbox_root(tmp_path: Path):
    dropbox_root = tmp_path / "Dropbox (Test)"
    base_dir = dropbox_root / "App" / "user_artifacts" / "interviews"
    settings = {
        "Hawthorne": {
            "interview_notes_dir": r"\LPL HAW Office Shared Docs\Staff\Candidates",
        }
    }

    resolved = resolve_interview_notes_output_dir(base_dir, "Hawthorne", settings)

    assert resolved == dropbox_root / "LPL HAW Office Shared Docs" / "Staff" / "Candidates"


def test_resolve_interview_notes_output_dir_drops_portable_dropbox_prefix(tmp_path: Path):
    dropbox_root = tmp_path / "Dropbox"
    base_dir = dropbox_root / "App" / "interviews"
    settings = {
        "North Long Beach": {
            "interview_notes_dir": r"\Dropbox\LPL NLB Office Shared\Staff\Candidates",
        }
    }

    resolved = resolve_interview_notes_output_dir(base_dir, "North Long Beach", settings)

    assert resolved == dropbox_root / "LPL NLB Office Shared" / "Staff" / "Candidates"




def test_school_email_template_store_save_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "school_email_template_settings.json"
    store = SchoolEmailTemplateStore(path)

    payload = {
        "north-campus": {
            "director_referral_subject_template": "Director Referral: {candidate_name}",
            "director_referral_body_template": "Please review {candidate_name}.",
            "director_email_to": "director@example.org",
            "offer_approval_subject_template": "Approval needed for {candidate_name}",
            "offer_approval_body_template": "Approve attached offer for {candidate_name}.",
            "offer_acceptance_subject_template": "Accepted: {candidate_name}",
            "offer_acceptance_body_template": "Candidate accepted.",
            "offer_email_to": "offers@example.org",
            "welcome_email_subject_template": "Welcome {candidate_name}",
            "welcome_email_body_template": "Welcome aboard!",
        }
    }
    store.save(payload)

    assert store.load() == payload

def test_json_store_atomic_write_json_keeps_expected_format(tmp_path: Path):
    assert JsonStore is onboarding_operations.JsonStore
    path = tmp_path / "onboarding_data.json"
    JsonStore._atomic_write_json(path, {"name": "Zoë", "active": True})

    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed == {"name": "Zoë", "active": True}


def test_safe_read_json_type_fallback(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")

    assert safe_read_json(path, default={}, expected_type=dict) == {}


def test_json_store_load_legacy_state_defaults_new_fields(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "employees": [],
                "templates": [],
                "monthly_last_sent": "2026-01-01",
            }
        ),
        encoding="utf-8",
    )

    store = JsonStore(tmp_path)
    state = store.load()

    assert state.monthly_last_sent == "2026-01-01"
    assert state.last_reminder_run_at is None
    assert state.reminder_run_history == []
    assert state.scheduler_settings == {}
    assert state.scheduler_status == {}


def test_json_store_save_persists_scheduler_and_reminder_fields(tmp_path: Path):
    store = JsonStore(tmp_path)
    state = store.load()
    state.last_reminder_run_at = "2026-02-14T09:00:00Z"
    state.reminder_run_history = [
        {
            "run_id": "run_1",
            "ran_at": "2026-02-14T09:00:00Z",
            "dry_run": False,
            "recipients": {"reminder": ["owner@example.com"]},
            "tasks": [
                {
                    "employee_id": "emp_1",
                    "employee_name": "Alex",
                    "task_id": "task_1",
                    "title": "Setup email",
                    "due_date": "2026-02-15",
                }
            ],
            "counts": {"due_reminders": 1},
            "outcomes": [
                {
                    "phase": "reminder",
                    "attempted": True,
                    "success": True,
                    "recipients": ["owner@example.com"],
                    "item_count": 1,
                    "message": "Sent",
                    "error": "",
                }
            ],
        }
    ]
    state.scheduler_settings = {"opt_in": True, "run_interval_minutes": 15}
    state.scheduler_status = {"enabled": True, "last_error": ""}

    store.save(state)

    written = json.loads((tmp_path / "onboarding_data.json").read_text(encoding="utf-8"))
    assert written["last_reminder_run_at"] == "2026-02-14T09:00:00+00:00"
    assert written["reminder_run_history"][0]["run_id"] == "run_1"
    assert written["scheduler_settings"] == {"opt_in": True, "run_interval_minutes": 15}
    assert written["scheduler_status"] == {"enabled": True, "last_error": ""}


def test_json_store_load_backfills_known_legacy_template_and_task_metadata(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "id": "setup_email",
                        "title": "Set up employee email",
                        "reference": "start_date",
                    }
                ],
                "employees": [
                    {
                        "id": "emp_1",
                        "name": "Alex",
                        "acceptance_date": "2026-01-01",
                        "start_date": "2026-01-10",
                        "tasks": [
                            {
                                "id": "task_1",
                                "template_id": "setup_email",
                                "title": "Set up employee email",
                                "due_date": "2026-01-09",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()

    assert state.templates[0].critical is True
    assert state.templates[0].deadline_label == "Before day 1"
    assert state.employees[0].tasks[0].critical is True
    assert state.employees[0].tasks[0].deadline_label == "Before day 1"

    overview = build_onboarding_overview(state.employees, today=date(2026, 1, 10))
    assert overview.total_critical_overdue == 1
    assert overview.employee_summaries[0].critical_overdue == 1


def test_json_store_load_does_not_backfill_unknown_template_metadata(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "id": "custom_unknown",
                        "title": "Custom onboarding task",
                        "reference": "start_date",
                    }
                ],
                "employees": [
                    {
                        "id": "emp_1",
                        "name": "Alex",
                        "acceptance_date": "2026-01-01",
                        "start_date": "2026-01-10",
                        "tasks": [
                            {
                                "id": "task_1",
                                "template_id": "custom_unknown",
                                "title": "Custom onboarding task",
                                "due_date": "2026-01-09",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()

    assert state.templates[0].critical is False
    assert state.templates[0].deadline_label is None
    assert state.employees[0].tasks[0].critical is False
    assert state.employees[0].tasks[0].deadline_label is None


def test_json_store_load_respects_explicit_user_override_for_known_template(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "id": "setup_email",
                        "title": "Set up employee email",
                        "reference": "start_date",
                        "critical": False,
                        "deadline_label": "Later",
                    }
                ],
                "employees": [
                    {
                        "id": "emp_1",
                        "name": "Alex",
                        "acceptance_date": "2026-01-01",
                        "start_date": "2026-01-10",
                        "tasks": [
                            {
                                "id": "task_1",
                                "template_id": "setup_email",
                                "title": "Set up employee email",
                                "critical": False,
                                "deadline_label": "Custom",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()

    assert state.templates[0].critical is False
    assert state.templates[0].deadline_label == "Later"
    assert state.employees[0].tasks[0].critical is False
    assert state.employees[0].tasks[0].deadline_label == "Custom"


def test_json_store_load_migration_is_idempotent(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "id": "setup_email",
                        "title": "Set up employee email",
                        "reference": "start_date",
                    }
                ],
                "employees": [
                    {
                        "id": "emp_1",
                        "name": "Alex",
                        "acceptance_date": "2026-01-01",
                        "start_date": "2026-01-10",
                        "tasks": [
                            {
                                "id": "task_1",
                                "template_id": "setup_email",
                                "title": "Set up employee email",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    store = JsonStore(tmp_path)
    store.load()
    first_contents = data_path.read_text(encoding="utf-8")

    store.load()
    second_contents = data_path.read_text(encoding="utf-8")

    assert first_contents == second_contents


def test_json_store_load_scheduler_defaults_align_with_ui_helpers(tmp_path: Path):
    state = JsonStore(tmp_path).load()

    assert scheduler_opt_in(state.scheduler_settings) is False
    assert scheduler_expected_interval_hours(state.scheduler_settings) == DEFAULT_EXPECTED_INTERVAL_HOURS


def test_json_store_save_load_round_trip_scheduler_settings_and_status(tmp_path: Path):
    store = JsonStore(tmp_path)
    state = store.load()
    state.scheduler_settings = {
        "enabled": True,
        "expected_interval_hours": 6,
    }
    state.scheduler_status = {
        "last_scheduler_run_at": "2026-02-20T14:15:00Z",
        "last_scheduler_result": "sent",
        "last_error": "",
        "last_run_source": "scheduler",
    }

    store.save(state)
    loaded = store.load()

    assert loaded.scheduler_settings == state.scheduler_settings
    assert loaded.scheduler_status == state.scheduler_status


def test_json_store_load_scheduler_interval_invalid_values_fallback_to_default(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "scheduler_settings": {"enabled": True, "expected_interval_hours": "abc"},
                "scheduler_status": {},
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()
    assert scheduler_expected_interval_hours(state.scheduler_settings) == DEFAULT_EXPECTED_INTERVAL_HOURS

    data_path.write_text(
        json.dumps(
            {
                "scheduler_settings": {"enabled": True, "expected_interval_hours": -4},
                "scheduler_status": {},
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()
    assert scheduler_expected_interval_hours(state.scheduler_settings) == DEFAULT_EXPECTED_INTERVAL_HOURS


def test_json_store_load_non_dict_scheduler_payloads_fallback_safely(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "scheduler_settings": ["invalid"],
                "scheduler_status": "invalid",
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()
    assert state.scheduler_settings == {}
    assert state.scheduler_status == {}


def test_json_store_load_legacy_run_interval_minutes_interprets_to_expected_interval_hours(tmp_path: Path):
    data_path = tmp_path / "onboarding_data.json"
    data_path.write_text(
        json.dumps(
            {
                "scheduler_settings": {"enabled": True, "run_interval_minutes": 180},
                "scheduler_status": {"last_scheduler_result": "dry_run"},
            }
        ),
        encoding="utf-8",
    )

    state = JsonStore(tmp_path).load()

    assert state.scheduler_settings["run_interval_minutes"] == 180
    assert scheduler_expected_interval_hours(state.scheduler_settings) == 3
