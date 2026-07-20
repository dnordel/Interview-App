from onboarding_operations import ReminderRecipientOutcome, ReminderRunSummary
from onboarding_operations import build_scheduler_status, normalize_run_source


def test_build_scheduler_status_for_successful_send():
    summary = ReminderRunSummary(
        ran_at="2026-01-01T00:00:00",
        dry_run=False,
        counts={"due_reminders": 2},
        outcomes=[ReminderRecipientOutcome(phase="reminder", success=True)],
    )

    status = build_scheduler_status(summary, scheduler_enabled=True, run_source="scheduler")

    assert status["enabled"] is True
    assert status["last_scheduler_result"] == "success"
    assert status["last_scheduler_run_at"] == "2026-01-01T00:00:00"
    assert status["last_run_source"] == "scheduler"
    assert "last_error" not in status


def test_build_scheduler_status_for_no_due_items_and_failed_outcome():
    no_due = ReminderRunSummary(ran_at="2026-01-01T00:00:00", dry_run=False, counts={"due_reminders": 0}, outcomes=[])
    failed = ReminderRunSummary(
        ran_at="2026-01-02T00:00:00",
        dry_run=False,
        counts={"due_reminders": 1},
        outcomes=[ReminderRecipientOutcome(phase="reminder", success=False, error="SMTP unavailable")],
    )

    no_due_status = build_scheduler_status(no_due, scheduler_enabled=False, run_source="manual")
    failed_status = build_scheduler_status(failed, scheduler_enabled=True, run_source="manual")

    assert no_due_status["last_scheduler_result"] == "no_due_items"
    assert failed_status["last_scheduler_result"] == "failed"
    assert failed_status["last_error"] == "SMTP unavailable"


def test_build_scheduler_status_for_dry_run_and_normalize_source():
    summary = ReminderRunSummary(ran_at="2026-01-03T00:00:00", dry_run=True, counts={"due_reminders": 3}, outcomes=[])

    status = build_scheduler_status(summary, scheduler_enabled=True, run_source=normalize_run_source("unknown"))

    assert status["last_scheduler_result"] == "dry_run"
    assert status["last_run_source"] == "manual"
