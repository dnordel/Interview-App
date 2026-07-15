import json
from pathlib import Path

from data_store import InterviewHistoryStore
from hiring_migration import HiringMigrationCoordinator
from hiring_pipeline import HiringPipelineStore


def test_guarded_migration_backs_up_valid_db_and_writes_redacted_parity_report(tmp_path: Path) -> None:
    artifacts = tmp_path / "user_artifacts"
    db_path = artifacts / "interview_history.sqlite3"
    InterviewHistoryStore(db_path).append(
        {
            "history_id": "hist-migrate",
            "candidate_name": "Private Candidate",
            "candidate_email": "private@example.com",
            "candidate_phone": "555-0100",
            "school": "Palmdale",
            "position": "Preschool",
            "score": 70,
            "outcome": "Hire",
        }
    )

    result = HiringMigrationCoordinator(db_path).run()

    assert result.committed is True
    assert result.integrity_status == "ok"
    assert result.idempotent is True
    assert result.backup_path.is_file()
    assert result.report_path.is_file()
    report_text = result.report_path.read_text(encoding="utf-8")
    assert "Private Candidate" not in report_text
    assert "private@example.com" not in report_text
    assert "555-0100" not in report_text
    report = json.loads(report_text)
    assert report["source_rows"] == 1
    assert report["application_count"] == 1
    assert report["backup_path"] == result.backup_path.name
    assert len(HiringPipelineStore(db_path).list_applications(include_archived=True)) == 1
