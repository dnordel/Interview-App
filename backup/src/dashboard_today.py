from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from onboarding_models import Employee
from onboarding_ui_helpers import task_status

DEFAULT_CRITICAL_WINDOW_DAYS = 3


@dataclass(slots=True)
class InterviewDashboardCounts:
    pending: int = 0
    follow_up: int = 0


@dataclass(slots=True)
class NextCriticalTask:
    employee_id: str
    employee_name: str
    task_id: str
    title: str
    due_date: str
    status: str


@dataclass(slots=True)
class OnboardingDashboardCounts:
    overdue: int = 0
    due_today: int = 0
    critical: int = 0
    critical_window_days: int = DEFAULT_CRITICAL_WINDOW_DAYS
    next_critical: NextCriticalTask | None = None


@dataclass(slots=True)
class DashboardTodaySummary:
    interviews: InterviewDashboardCounts
    onboarding: OnboardingDashboardCounts


def build_dashboard_today_summary(
    history_rows: list[dict[str, Any]],
    employees: list[Employee],
    scheduler_settings: Mapping[str, Any] | None,
    today: date | None = None,
) -> DashboardTodaySummary:
    current_day = today or date.today()
    interview_counts = summarize_interview_states(history_rows)
    window_days = critical_window_days_from_settings(scheduler_settings)
    onboarding_counts = summarize_onboarding_states(employees, current_day, window_days)
    return DashboardTodaySummary(interviews=interview_counts, onboarding=onboarding_counts)


def summarize_interview_states(history_rows: list[dict[str, Any]]) -> InterviewDashboardCounts:
    pending = 0
    follow_up = 0
    for row in history_rows:
        status = str(row.get("interview_status", "")).strip().lower()
        if status in {"pending", "scheduled"}:
            pending += 1

        flagged_for_follow_up = bool(row.get("follow_up_required", False))
        if flagged_for_follow_up or status == "follow_up":
            follow_up += 1
    return InterviewDashboardCounts(pending=pending, follow_up=follow_up)


def summarize_onboarding_states(
    employees: list[Employee],
    today: date,
    critical_window_days: int,
) -> OnboardingDashboardCounts:
    overdue = 0
    due_today = 0
    critical = 0
    next_item: tuple[date, NextCriticalTask] | None = None

    for employee in employees:
        for task in employee.tasks:
            status = task_status(task, today)
            if status in {"completed", "unscheduled", "upcoming"}:
                continue

            if status == "overdue":
                overdue += 1
            elif status == "due_today":
                due_today += 1

            if not _is_critical_by_window(task.due_date, today, critical_window_days):
                continue

            critical += 1
            candidate_due = _parse_due_date(task.due_date)
            if candidate_due is None:
                continue
            candidate = NextCriticalTask(
                employee_id=employee.id,
                employee_name=employee.name,
                task_id=task.id,
                title=task.title,
                due_date=task.due_date,
                status=status,
            )
            if _is_earlier_task(next_item, candidate_due, candidate.title):
                next_item = (candidate_due, candidate)

    return OnboardingDashboardCounts(
        overdue=overdue,
        due_today=due_today,
        critical=critical,
        critical_window_days=critical_window_days,
        next_critical=next_item[1] if next_item else None,
    )


def critical_window_days_from_settings(settings: Mapping[str, Any] | None) -> int:
    if not settings:
        return DEFAULT_CRITICAL_WINDOW_DAYS

    candidate = settings.get("critical_window_days", DEFAULT_CRITICAL_WINDOW_DAYS)
    if not isinstance(candidate, int):
        return DEFAULT_CRITICAL_WINDOW_DAYS
    if candidate < 1:
        return DEFAULT_CRITICAL_WINDOW_DAYS
    return candidate


def _is_critical_by_window(due_date_text: str | None, today: date, window_days: int) -> bool:
    due_date = _parse_due_date(due_date_text)
    if due_date is None:
        return False
    return (due_date - today).days <= window_days


def _parse_due_date(due_date_text: str | None) -> date | None:
    if not due_date_text:
        return None
    try:
        return date.fromisoformat(due_date_text)
    except ValueError:
        return None


def _is_earlier_task(current: tuple[date, NextCriticalTask] | None, due: date, title: str) -> bool:
    if current is None:
        return True
    current_due, current_item = current
    if due < current_due:
        return True
    if due > current_due:
        return False
    return title.lower() < current_item.title.lower()
