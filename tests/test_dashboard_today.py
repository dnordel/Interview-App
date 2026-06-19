from datetime import date

from onboarding_operations import (
    build_dashboard_today_summary,
    critical_window_days_from_settings,
    summarize_interview_states,
)
from onboarding_operations import Employee, EmployeeTask


def test_interview_state_defaults_to_zero_without_tracking_fields():
    rows = [{"candidate_name": "Alex"}]
    summary = summarize_interview_states(rows)
    assert summary.pending == 0
    assert summary.follow_up == 0


def test_interview_state_counts_pending_and_follow_up_fields_when_present():
    rows = [
        {"interview_status": "pending"},
        {"interview_status": "scheduled"},
        {"interview_status": "follow_up"},
        {"follow_up_required": True},
    ]
    summary = summarize_interview_states(rows)
    assert summary.pending == 2
    assert summary.follow_up == 2


def test_dashboard_counts_overdue_due_today_and_critical_with_custom_window():
    employee = Employee(
        id="emp-1",
        name="Taylor",
        acceptance_date="2026-01-01",
        start_date="2026-01-10",
        tasks=[
            EmployeeTask(id="t-over", template_id="", title="Past", due_date="2026-01-09"),
            EmployeeTask(id="t-today", template_id="", title="Today", due_date="2026-01-10"),
            EmployeeTask(id="t-soon", template_id="", title="Soon", due_date="2026-01-12"),
            EmployeeTask(id="t-late", template_id="", title="Later", due_date="2026-01-20"),
        ],
    )
    summary = build_dashboard_today_summary(
        history_rows=[],
        employees=[employee],
        scheduler_settings={"critical_window_days": 2},
        today=date(2026, 1, 10),
    )
    assert summary.onboarding.overdue == 1
    assert summary.onboarding.due_today == 1
    assert summary.onboarding.critical == 3
    assert summary.onboarding.next_critical is not None
    assert summary.onboarding.next_critical.title == "Past"


def test_critical_window_days_setting_falls_back_to_default():
    assert critical_window_days_from_settings({"critical_window_days": 0}) == 3
    assert critical_window_days_from_settings({"critical_window_days": "x"}) == 3
    assert critical_window_days_from_settings(None) == 3
