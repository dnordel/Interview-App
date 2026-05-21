from datetime import date

from onboarding_models import Employee, EmployeeTask
from ux_metrics import (
    EVENT_TASK_OVERDUE,
    SCOPE_CREATED_MONTH,
    SCOPE_EVENT_MONTH,
    UxMetricsLogger,
    build_monthly_summary,
)


def test_log_overdue_once(tmp_path):
    logger = UxMetricsLogger(tmp_path)
    first = logger.log_overdue_once(task_id="t1", due_date="2026-01-01", task_type="setup")
    second = logger.log_overdue_once(task_id="t1", due_date="2026-01-01", task_type="setup")

    assert first is True
    assert second is False
    events = logger.read_events()
    assert len(events) == 1
    assert events[0]["event_type"] == EVENT_TASK_OVERDUE


def test_monthly_summary_supports_both_scopes():
    employee = Employee(
        id="emp1",
        name="A",
        acceptance_date="2026-01-01",
        start_date="2026-01-05",
        tasks=[
            EmployeeTask(
                id="task1",
                template_id="benefits_invite",
                title="Benefits invite",
                created_at="2026-01-01",
                due_date="2026-01-02",
                completed=True,
                completed_at="2026-01-02",
            ),
            EmployeeTask(
                id="task2",
                template_id="setup_email",
                title="Set up employee email",
                created_at="2026-01-01",
                due_date="2026-01-02",
                completed=True,
                completed_at="2026-01-05",
            ),
        ],
    )

    events = [
        {"event_type": EVENT_TASK_OVERDUE, "timestamp": "2026-01-03T00:00:00Z"},
        {"event_type": EVENT_TASK_OVERDUE, "timestamp": "2025-12-15T00:00:00Z"},
    ]

    event_scope = build_monthly_summary(
        month=date(2026, 1, 1),
        scope=SCOPE_EVENT_MONTH,
        employees=[employee],
        events=events,
    )
    created_scope = build_monthly_summary(
        month=date(2026, 1, 1),
        scope=SCOPE_CREATED_MONTH,
        employees=[employee],
        events=events,
    )

    assert event_scope.overdue_count == 1
    assert created_scope.on_time_completion_pct == 50.0
    assert "benefits_invite" in created_scope.median_days_by_task_type


def test_ux_wrapper_event_naming_and_required_fields(tmp_path):
    logger = UxMetricsLogger(tmp_path)

    logger.log_ux_view(app="interview", surface="trait_screen", target="page_load")
    logger.log_ux_click(app="onboarding", surface="today_dashboard", target="overdue")
    logger.log_ux_validation_error(app="onboarding", surface="reminders", error_type="invalid_recipients")
    logger.log_ux_completion(app="interview", surface="finalize", outcome="completed")
    logger.log_ux_click(app="onboarding", surface="today_dashboard", target="")

    event_types = [item["event_type"] for item in logger.read_events()]
    assert "ux.interview.trait_screen.view" in event_types
    assert "ux.onboarding.today_dashboard.click" in event_types
    assert "ux.onboarding.reminders.validation_error" in event_types
    assert "ux.interview.finalize.completion" in event_types
    assert event_types.count("ux.onboarding.today_dashboard.click") == 1


def test_ux_wrapper_blocks_identifying_and_free_text_fields(tmp_path):
    logger = UxMetricsLogger(tmp_path)

    logger.log_ux_completion(
        app="interview",
        surface="settings",
        outcome="saved",
        candidate_name="Secret Candidate",
        notes="Long free text should be blocked",
        debug_notes="also blocked",
        email="private@example.com",
        mode="manual",
    )

    event = logger.read_events()[0]
    assert event["event_type"] == "ux.interview.settings.completion"
    assert "candidate_name" not in event
    assert "notes" not in event
    assert "debug_notes" not in event
    assert "email" not in event
    assert event["mode"] == "manual"


def test_onboarding_canonical_event_shim_and_schema_coercion(tmp_path):
    logger = UxMetricsLogger(tmp_path)

    logger.log_event(
        "onboarding_sender_email_validation_error",
        error_reason="INVALID_FORMAT",
        attempt_count="2",
        email="sensitive@example.com",
    )

    event = logger.read_events()[0]
    assert event["event_type"] == "ux.onboarding.sender_email.validation_error"
    assert event["error_reason"] == "invalid_format"
    assert event["attempt_count"] == 2
    assert "email" not in event


def test_log_onboarding_canonical_event_emits_allowed_fields_only(tmp_path):
    logger = UxMetricsLogger(tmp_path)

    logger.log_onboarding_canonical_event(
        "ux.onboarding.reminder_run.completion",
        mode="LIVE",
        recipient_count="3",
        skipped_count="1",
        warning_count="0",
        sent_count="2",
        failed_count="1",
        blocked_count="0",
        free_text="do not keep",
    )

    event = logger.read_events()[0]
    assert event["event_type"] == "ux.onboarding.reminder_run.completion"
    assert event["mode"] == "live"
    assert event["recipient_count"] == 3
    assert "free_text" not in event
