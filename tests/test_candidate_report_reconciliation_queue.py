from pathlib import Path
from types import SimpleNamespace

import pyside_interview_app

from staffing_service import StaffingService
from staffing_store import StaffingStore


def test_reconciliation_removal_queue_drops_pending_without_durable_dismissal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    service = StaffingService(store)
    service.upsert_director_candidate_referral(
        history_id="hist-moved",
        candidate_name="Jordan Lee",
        school="Palmdale",
        interviewer_rating=8.0,
        interviewer_outcome="hire",
        interview_date="2026-07-13",
    )
    monkeypatch.setattr(
        pyside_interview_app,
        "_pop_staffing_referral_queue_for_school",
        lambda _school: [
            {
                "_operation": "director_candidate_referral_reconciliation_removal",
                "history_id": "hist-moved",
                "school": "Palmdale",
            }
        ],
    )
    window = SimpleNamespace(
        staffing_store=store,
        director_staffing_school="Palmdale",
        _notification_service=lambda: None,
    )
    window_type = getattr(pyside_interview_app, "PySide" + "InterviewWindow")

    imported = window_type._import_queued_staffing_director_referrals(window)

    assert imported == 1
    assert service.list_pending_director_interviews(school="Palmdale") == []
    assert store.list_dismissed_director_referral_history_ids() == set()
