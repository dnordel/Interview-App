from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from onboarding_models import Employee, EmployeeTask, parse_date


TASK_STATUS_ORDER = {
    "overdue": 0,
    "due_today": 1,
    "due_soon": 2,
    "upcoming": 3,
    "unscheduled": 4,
    "completed": 5,
}

TASK_STATUS_COLORS = {
    "overdue": "#991B1B",
    "due_today": "#9A3412",
    "due_soon": "#92400E",
    "upcoming": "#1D4ED8",
    "unscheduled": "#475569",
    "completed": "#166534",
}

TASK_STATUS_LABELS = {
    "overdue": "Overdue",
    "due_today": "Due today",
    "due_soon": "Due within 3 days",
    "upcoming": "Upcoming",
    "unscheduled": "No due date",
    "completed": "Completed",
}

TASK_STATUS_ICONS = {
    "overdue": "⚠",
    "due_today": "●",
    "due_soon": "◔",
    "upcoming": "○",
    "unscheduled": "–",
    "completed": "✓",
}

TASK_STATUS_BADGE_STYLE = {
    "overdue": {"bg": "#7F1D1D", "fg": "#FFFFFF"},
    "due_today": {"bg": "#9A3412", "fg": "#FFFFFF"},
    "due_soon": {"bg": "#78350F", "fg": "#FFFFFF"},
    "upcoming": {"bg": "#1E40AF", "fg": "#FFFFFF"},
    "unscheduled": {"bg": "#334155", "fg": "#FFFFFF"},
    "completed": {"bg": "#14532D", "fg": "#FFFFFF"},
}


def task_status_badge_text(status: str) -> str:
    icon = TASK_STATUS_ICONS.get(status, "•")
    label = TASK_STATUS_LABELS.get(status, status.title())
    return f"{icon} {label}"


@dataclass(slots=True)
class EmployeeTaskSummary:
    employee_id: str
    employee_name: str
    overdue: int
    critical_overdue: int
    due_today: int
    due_soon: int


@dataclass(slots=True)
class OnboardingOverview:
    total_overdue: int
    total_critical_overdue: int
    total_due_today: int
    total_due_soon: int
    employee_summaries: list[EmployeeTaskSummary]


def task_status(task: EmployeeTask, today: date) -> str:
    if task.completed:
        return "completed"

    if not task.due_date:
        return "unscheduled"

    due_date = parse_date(task.due_date)
    if due_date < today:
        return "overdue"

    if due_date == today:
        return "due_today"

    if (due_date - today).days <= 3:
        return "due_soon"

    return "upcoming"


def sorted_tasks_for_display(tasks: list[EmployeeTask], today: date) -> list[EmployeeTask]:
    return sorted(tasks, key=lambda item: _task_sort_key(item, today))


def _task_sort_key(task: EmployeeTask, today: date) -> tuple[int, date, str]:
    status = task_status(task, today)
    order = TASK_STATUS_ORDER[status]
    due = parse_date(task.due_date) if task.due_date else date.max
    return order, due, task.title.lower()


def build_onboarding_overview(employees: list[Employee], today: date) -> OnboardingOverview:
    summaries: list[EmployeeTaskSummary] = []
    total_overdue = 0
    total_critical_overdue = 0
    total_due_today = 0
    total_due_soon = 0

    for employee in employees:
        summary = _summarize_employee_tasks(employee, today)
        if _is_empty_summary(summary):
            continue
        summaries.append(summary)
        total_overdue += summary.overdue
        total_critical_overdue += summary.critical_overdue
        total_due_today += summary.due_today
        total_due_soon += summary.due_soon

    summaries.sort(
        key=lambda row: (
            -row.critical_overdue,
            -row.overdue,
            -row.due_today,
            -row.due_soon,
            row.employee_name.lower(),
        )
    )
    return OnboardingOverview(
        total_overdue=total_overdue,
        total_critical_overdue=total_critical_overdue,
        total_due_today=total_due_today,
        total_due_soon=total_due_soon,
        employee_summaries=summaries,
    )


def _summarize_employee_tasks(employee: Employee, today: date) -> EmployeeTaskSummary:
    overdue = 0
    critical_overdue = 0
    due_today = 0
    due_soon = 0

    for task in employee.tasks:
        status = task_status(task, today)
        if status == "overdue":
            overdue += 1
            if task.critical:
                critical_overdue += 1
            continue
        if status == "due_today":
            due_today += 1
            continue
        if status == "due_soon":
            due_soon += 1

    return EmployeeTaskSummary(
        employee_id=employee.id,
        employee_name=employee.name,
        overdue=overdue,
        critical_overdue=critical_overdue,
        due_today=due_today,
        due_soon=due_soon,
    )


def _is_empty_summary(summary: EmployeeTaskSummary) -> bool:
    return summary.overdue == 0 and summary.due_today == 0 and summary.due_soon == 0
