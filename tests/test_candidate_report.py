from __future__ import annotations

import sqlite3
import csv
from pathlib import Path

import pytest

from candidate_report import (
    CandidateReportDifference,
    CandidateReportPermissionError,
    CandidateReportRepository,
    CandidateReportStaleError,
    CandidateReportValidationError,
    build_candidate_report_snapshot,
    export_candidate_report_audit_csv,
    recalculate_candidate_report,
    resolve_legacy_report_path,
)


def _snapshot() -> dict:
    return {
        "schema_version": 1,
        "history_id": "hist-1",
        "candidate": {"candidate_name": "Candidate", "school": "Palmdale", "track": "preschool"},
        "questions": [
            {
                "question_id": "q1",
                "type": "trait",
                "rating": 4,
                "weight": 2,
                "skipped": False,
                "skip_reason": "",
                "absolute_disqualifier": False,
                "interviewer_notes": "Clear example.",
                "transcript": "Current text",
                "original_transcript": "Original text",
            }
        ],
        "scoring": {"percent_of_max": 80, "outcome": "Hire", "rows": []},
        "summaries": {"executive_summary": "Summary", "review_needed": False},
        "report_path": "report.docx",
    }


def _repo(tmp_path: Path) -> CandidateReportRepository:
    repo = CandidateReportRepository(tmp_path / "interview_history.sqlite3")
    repo.initialize()
    with sqlite3.connect(repo.db_path) as conn:
        CandidateReportRepository.insert_initial_on_connection(
            conn, "hist-1", _snapshot(), actor="admin-user", actor_role="admin", app_version="1.0"
        )
        conn.commit()
    return repo


def test_build_candidate_report_snapshot_preserves_original_transcript():
    snapshot = build_candidate_report_snapshot(
        {
            "candidate": {"name": "A", "school": "Palmdale", "qualification": {"ece_units_completed": 12}},
            "flow_transcript": [
                {"flow_index": 1, "id": "q1", "type": "trait", "prompt": "Question?", "candidate_transcript": "Answer"}
            ],
        },
        {"outcome": "Hire", "rows": [{"trait_id": "q1", "raw_score": 4, "weight": 2, "weighted_score": 8}]},
        {"history_id": "hist-1", "saved_at": "2026-01-01T00:00:00Z"},
        report_path="report.docx",
    )

    assert snapshot["questions"][0]["transcript"] == "Answer"
    assert snapshot["questions"][0]["original_transcript"] == "Answer"
    assert snapshot["questions"][0]["rating"] == 4
    assert snapshot["candidate"]["qualification"]["ece_units_completed"] == 12


def test_sync_imported_transcripts_updates_director_snapshot_and_preserves_scores(tmp_path: Path):
    repo = _repo(tmp_path)

    updated = repo.sync_imported_transcripts(
        "hist-1",
        {"q1": "Imported candidate answer"},
        app_version="test",
    )

    director = repo.load_visible_version("hist-1", role="director", school_scope="Palmdale")
    question = director.snapshot["questions"][0]
    assert updated.version_number == 2
    assert question["transcript"] == "Imported candidate answer"
    assert question["original_transcript"] == "Imported candidate answer"
    assert question["rating"] == 4
    assert repo.list_audit_events("hist-1")[0].action == "report_transcripts_imported"


def test_director_sees_finalized_snapshot_while_admin_draft_exists(tmp_path: Path):
    repo = _repo(tmp_path)
    initial = repo.load_visible_version("hist-1", role="admin")
    repo.reopen(
        "hist-1", expected_row_version=initial.row_version, reason="Correct score", actor="admin-user", role="admin"
    )
    reopened = repo.load_visible_version("hist-1", role="admin")
    edited = dict(reopened.snapshot)
    edited["candidate"] = {**edited["candidate"], "candidate_name": "Corrected Candidate"}
    repo.save_draft(
        "hist-1", edited, expected_row_version=reopened.row_version, actor="admin-user", role="admin"
    )

    assert repo.load_visible_version("hist-1", role="admin").snapshot["candidate"]["candidate_name"] == "Corrected Candidate"
    assert repo.load_visible_version("hist-1", role="director").snapshot["candidate"]["candidate_name"] == "Candidate"


def test_director_cannot_mutate_initial_report_and_school_scope_is_enforced(tmp_path: Path):
    repo = _repo(tmp_path)
    record = repo.load_visible_version("hist-1", role="director", school_scope="Palmdale")
    with pytest.raises(CandidateReportPermissionError):
        repo.save_draft(
            "hist-1", record.snapshot, expected_row_version=record.row_version, actor="director-user", role="director"
        )
    with pytest.raises(CandidateReportPermissionError):
        repo.load_visible_version("hist-1", role="director", school_scope="Hawthorne")


def test_stale_write_fails_but_admin_force_creates_new_version(tmp_path: Path):
    repo = _repo(tmp_path)
    initial = repo.load_visible_version("hist-1", role="admin")
    reopened = repo.reopen(
        "hist-1", expected_row_version=initial.row_version, reason="Correction", actor="admin-user", role="admin"
    )
    with pytest.raises(CandidateReportStaleError):
        repo.save_draft(
            "hist-1", reopened.snapshot, expected_row_version=initial.row_version, actor="admin-user", role="admin"
        )
    forced = repo.save_draft(
        "hist-1", reopened.snapshot, expected_row_version=initial.row_version,
        actor="admin-user", role="admin", force=True, reason="Preserve local correction"
    )
    assert forced.version_number == 3


def test_compare_version_returns_ordered_field_differences(tmp_path: Path):
    repo = _repo(tmp_path)
    saved = repo.load_visible_version("hist-1", role="admin")
    reopened = repo.reopen(
        "hist-1", expected_row_version=saved.row_version, reason="Other editor", actor="other-admin", role="admin"
    )
    current = {**reopened.snapshot, "candidate": {**reopened.snapshot["candidate"], "school": "Hawthorne"}}
    repo.save_draft(
        "hist-1", current, expected_row_version=reopened.row_version, actor="other-admin", role="admin"
    )
    local = {
        **saved.snapshot,
        "candidate": {**saved.snapshot["candidate"], "candidate_name": "Local Name"},
        "questions": [{**saved.snapshot["questions"][0], "rating": 2}],
    }

    differences = repo.compare_version(
        "hist-1", local, role="admin", saved_snapshot=saved.snapshot
    )

    assert all(isinstance(item, CandidateReportDifference) for item in differences)
    assert [item.field_path for item in differences] == ["candidate.candidate_name", "candidate.school", "questions.0.rating"]
    assert differences[0].saved_value == "Candidate"
    assert differences[0].current_value == "Candidate"
    assert differences[0].local_value == "Local Name"
    assert differences[1].current_value == "Hawthorne"
    assert differences[2].local_value == 2


def test_finalize_blocks_invalid_rating_and_missing_skip_reason(tmp_path: Path):
    repo = _repo(tmp_path)
    record = repo.reopen(
        "hist-1", expected_row_version=1, reason="Correction", actor="admin-user", role="admin"
    )
    invalid = dict(record.snapshot)
    invalid["questions"] = [
        {**record.snapshot["questions"][0], "rating": 9},
        {**record.snapshot["questions"][0], "question_id": "q2", "skipped": True, "rating": None, "skip_reason": ""},
    ]
    with pytest.raises(CandidateReportValidationError) as exc:
        repo.finalize(
            "hist-1", invalid, expected_row_version=record.row_version, actor="admin-user", role="admin"
        )
    assert {issue.code for issue in exc.value.issues} == {"invalid_rating", "missing_skip_reason"}


def test_save_changes_validates_but_keeps_report_private(tmp_path: Path):
    repo = _repo(tmp_path)
    reopened = repo.reopen(
        "hist-1", expected_row_version=1, reason="Correction", actor="admin-user", role="admin"
    )
    invalid = {**reopened.snapshot, "questions": [{**reopened.snapshot["questions"][0], "rating": 9}]}
    with pytest.raises(CandidateReportValidationError):
        repo.save_changes(
            "hist-1", invalid, expected_row_version=reopened.row_version, actor="admin-user", role="admin"
        )
    valid = {
        **reopened.snapshot,
        "candidate": {**reopened.snapshot["candidate"], "candidate_name": "Validated Draft"},
        "summaries": {**reopened.snapshot["summaries"], "review_needed": True},
    }

    saved = repo.save_changes(
        "hist-1", valid, expected_row_version=reopened.row_version, actor="admin-user", role="admin"
    )

    assert saved.state == "reopened"
    assert saved.snapshot["candidate"]["candidate_name"] == "Validated Draft"
    assert repo.load_visible_version("hist-1", role="director").snapshot["candidate"]["candidate_name"] == "Candidate"
    assert repo.list_audit_events("hist-1")[0].action == "changes_saved"


def test_finalize_appends_field_audit_and_immutable_version(tmp_path: Path):
    repo = _repo(tmp_path)
    record = repo.reopen(
        "hist-1", expected_row_version=1, reason="Correct candidate", actor="admin-user", role="admin"
    )
    edited = dict(record.snapshot)
    edited["candidate"] = {**edited["candidate"], "candidate_name": "Corrected"}
    finalized = repo.finalize(
        "hist-1", edited, expected_row_version=record.row_version,
        actor="admin-user", role="admin", reason="Name correction"
    )

    assert finalized.state == "finalized"
    assert [version.version_number for version in repo.list_versions("hist-1")] == [3, 2, 1]
    events = repo.list_audit_events("hist-1")
    assert any(event.field_path == "candidate.candidate_name" for event in events)
    assert events[0].reason == "Name correction"


def test_recalculate_candidate_report_uses_scoring_engine_and_marks_narratives_for_review():
    rubric = {
        "traits": [
            {"id": "q1", "name": "Q1", "priority": "non-critical", "weight": 1, "applicable_tracks": ["all"], "primary_question": "Q1"}
        ],
        "tracks": {"preschool": {"label": "Preschool", "max_weighted_total": 5}},
    }
    snapshot = _snapshot()
    snapshot["questions"][0]["rating"] = 4
    snapshot["questions"][0]["weight"] = 1

    recalculated = recalculate_candidate_report(snapshot, rubric=rubric, track_key="preschool")

    assert recalculated["scoring"]["percent_of_max"] == 80.0
    assert recalculated["scoring"]["outcome"] == "Hire"
    assert recalculated["summaries"]["review_needed"] is True


def test_legacy_report_path_requires_matching_school_existing_docx(tmp_path: Path):
    from data_store import InterviewHistoryStore

    history_path = tmp_path / "interview_history.sqlite3"
    report_path = tmp_path / "legacy.docx"
    report_path.write_bytes(b"legacy")
    InterviewHistoryStore(history_path).append(
        {
            "history_id": "legacy-1", "candidate_name": "Legacy", "school": "Hawthorne",
            "interview_notes_path": str(report_path), "saved_at": "2026-01-01T00:00:00Z",
        }
    )

    assert resolve_legacy_report_path(history_path, "legacy-1", school_scope="Hawthorne") == report_path.resolve()
    with pytest.raises(CandidateReportPermissionError):
        resolve_legacy_report_path(history_path, "legacy-1", school_scope="Palmdale")


def test_system_can_advance_finalized_word_path_without_reopening_report(tmp_path: Path):
    repo = _repo(tmp_path)
    replacement = tmp_path / "rich-report.docx"
    replacement.write_bytes(b"new")

    updated = repo.sync_report_path("hist-1", replacement, app_version="1.2")

    assert updated.state == "finalized"
    assert updated.snapshot["report_path"] == str(replacement.resolve())
    assert repo.load_visible_version("hist-1", role="director").snapshot["report_path"] == str(replacement.resolve())
    assert repo.list_audit_events("hist-1")[0].action == "report_document_updated"


def test_audit_csv_export_escapes_values_and_uses_explicit_destination(tmp_path: Path):
    destination = tmp_path / "audit.csv"
    exported = export_candidate_report_audit_csv(
        [
            {
                "created_at": "2026-07-13T10:00:00Z", "version": "4", "actor": "Admin, User",
                "actor_role": "admin", "action": "changes_saved", "field_path": "summaries.executive_summary",
                "old_value": "Old", "new_value": "Line 1\nLine 2", "reason": "Correction",
                "source": "administrator", "revision_id": "revision-1",
            }
        ],
        destination,
    )

    with exported.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert exported == destination
    assert rows[0]["User"] == "Admin, User"
    assert rows[0]["New Value"] == "Line 1\nLine 2"
