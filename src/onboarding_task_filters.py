from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from onboarding_models import EmployeeTask, parse_date
from onboarding_ui_helpers import task_status


@dataclass(frozen=True, slots=True)
class TaskFilterOption:
    key: str
    label: str
    hotkey: str


TASK_FILTER_OPTIONS = [
    TaskFilterOption(key="all", label="All", hotkey="1"),
    TaskFilterOption(key="pending", label="Pending", hotkey="2"),
    TaskFilterOption(key="urgent", label="Urgent", hotkey="3"),
    TaskFilterOption(key="overdue", label="Overdue", hotkey="4"),
    TaskFilterOption(key="due_today", label="Due Today", hotkey="5"),
    TaskFilterOption(key="due_soon", label="Due in 3 Days", hotkey="6"),
    TaskFilterOption(key="completed", label="Completed", hotkey="7"),
]

KPI_FILTER_ENTRYPOINTS: dict[str, str] = {
    "overdue": "overdue",
    "due_today": "due_today",
    "urgent": "urgent",
    "critical": "urgent",
    "pending": "pending",
}


def filtered_tasks(tasks: list[EmployeeTask], today: date, filter_key: str) -> list[EmployeeTask]:
    if filter_key == "all":
        selected = list(tasks)
    elif filter_key == "pending":
        selected = [task for task in tasks if task_status(task, today) != "completed"]
    elif filter_key == "urgent":
        selected = [
            task
            for task in tasks
            if task_status(task, today) in {"overdue", "due_today", "due_soon"}
        ]
    else:
        selected = [task for task in tasks if task_status(task, today) == filter_key]

    return sorted(selected, key=lambda task: task_display_sort_key(task, today))


def urgent_filter_result_count(tasks: list[EmployeeTask], today: date) -> int:
    # Canonical telemetry reference: ux.onboarding.urgent_filter.click
    return len(filtered_tasks(tasks, today, "urgent"))

def task_display_sort_key(task: EmployeeTask, today: date) -> tuple[int, date, str]:
    status = task_status(task, today)
    priority = _status_priority(status)
    due = parse_date(task.due_date) if task.due_date else date.max
    return priority, due, task.title.lower()


def format_due_date_short(due_date: str | None) -> str:
    if not due_date:
        return "—"
    return parse_date(due_date).strftime("%b %d")


def filter_for_dashboard_kpi(kpi_key: str) -> str | None:
    return KPI_FILTER_ENTRYPOINTS.get(kpi_key)


def _status_priority(status: str) -> int:
    if status == "overdue":
        return 0
    if status == "due_today":
        return 1
    if status == "due_soon":
        return 2
    if status == "upcoming":
        return 3
    if status == "unscheduled":
        return 4
    return 5
