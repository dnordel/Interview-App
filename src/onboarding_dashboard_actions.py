from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dashboard_today import DashboardTodaySummary
from onboarding_models import Employee
from onboarding_task_filters import filter_for_dashboard_kpi, filtered_tasks


@dataclass(frozen=True, slots=True)
class DashboardKpiChip:
    key: str
    label: str
    count: int
    filter_key: str


@dataclass(frozen=True, slots=True)
class DashboardRecommendation:
    message: str
    button_label: str
    action_key: str
    employee_id: str | None = None
    employee_name: str | None = None
    filter_key: str | None = None


def build_dashboard_kpi_chips(
    summary: DashboardTodaySummary,
    employees: list[Employee],
    today: date,
) -> list[DashboardKpiChip]:
    pending_count = _count_tasks_for_filter(employees, today, "pending")
    return [
        DashboardKpiChip(key="overdue", label="Overdue", count=summary.onboarding.overdue, filter_key="overdue"),
        DashboardKpiChip(key="due_today", label="Due Today", count=summary.onboarding.due_today, filter_key="due_today"),
        DashboardKpiChip(key="urgent", label=f"Urgent (≤{summary.onboarding.critical_window_days}d)", count=summary.onboarding.critical, filter_key="urgent"),
        DashboardKpiChip(key="pending", label="Pending", count=pending_count, filter_key="pending"),
    ]


def build_recommended_action(
    summary: DashboardTodaySummary,
    employees: list[Employee],
    today: date,
) -> DashboardRecommendation:
    overdue_target = first_matching_navigation(employees, today, "overdue")
    if overdue_target is not None:
        return DashboardRecommendation(
            message=f"{summary.onboarding.overdue} overdue tasks need attention.",
            button_label="Open Overdue Tasks",
            action_key="open_filtered_tasks",
            employee_id=overdue_target["employee_id"],
            employee_name=overdue_target["employee_name"],
            filter_key="overdue",
        )

    due_today_target = first_matching_navigation(employees, today, "due_today")
    if due_today_target is not None:
        return DashboardRecommendation(
            message=f"{summary.onboarding.due_today} tasks are due today.",
            button_label="Open Due Today",
            action_key="open_filtered_tasks",
            employee_id=due_today_target["employee_id"],
            employee_name=due_today_target["employee_name"],
            filter_key="due_today",
        )

    pending_target = first_matching_navigation(employees, today, "pending")
    if pending_target is not None:
        return DashboardRecommendation(
            message="No urgent deadlines. Keep momentum by finishing pending tasks.",
            button_label="Review Pending Tasks",
            action_key="open_filtered_tasks",
            employee_id=pending_target["employee_id"],
            employee_name=pending_target["employee_name"],
            filter_key="pending",
        )

    return DashboardRecommendation(
        message="No onboarding tasks are pending. Continue with interviews or setup work.",
        button_label="Start Interview",
        action_key="start_interview",
    )


def first_matching_navigation(employees: list[Employee], today: date, filter_key: str) -> dict[str, str] | None:
    for employee in sorted(employees, key=lambda row: row.name.lower()):
        matches = filtered_tasks(employee.tasks, today, filter_key)
        if not matches:
            continue
        return {
            "employee_id": employee.id,
            "employee_name": employee.name,
            "task_id": matches[0].id,
            "filter_key": filter_key,
        }
    return None


def kpi_navigation_target(
    employees: list[Employee],
    today: date,
    kpi_key: str,
) -> dict[str, str] | None:
    filter_key = filter_for_dashboard_kpi(kpi_key)
    if filter_key is None:
        return None
    return first_matching_navigation(employees, today, filter_key)


def _count_tasks_for_filter(employees: list[Employee], today: date, filter_key: str) -> int:
    count = 0
    for employee in employees:
        count += len(filtered_tasks(employee.tasks, today, filter_key))
    return count
