from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from onboarding_models import Employee, EmployeeTask, TaskTemplate, make_id, parse_date, to_date_str
from onboarding_template_reference import parse_specific_date_reference


@dataclass(slots=True)
class ReminderItem:
    employee_id: str
    employee_name: str
    task_id: str
    title: str
    due_date: str | None


def seed_employee_tasks(employee: Employee, templates: list[TaskTemplate]) -> None:
    for template in templates:
        due_date = calculate_due_date(employee, template, date.today())
        employee.tasks.append(
            EmployeeTask(
                id=make_id("task"),
                template_id=template.id,
                title=template.title,
                created_at=date.today().isoformat(),
                due_date=to_date_str(due_date),
                critical=template.critical,
                deadline_label=template.deadline_label,
            )
        )


def calculate_due_date(employee: Employee, template: TaskTemplate, now_date: date) -> date | None:
    specific_date = parse_specific_date_reference(template.reference)
    if specific_date is not None:
        return specific_date + timedelta(days=template.offset_days)
    if template.reference == "start_date":
        return parse_date(employee.start_date) + timedelta(days=template.offset_days)
    if template.reference == "acceptance_date":
        return parse_date(employee.acceptance_date) + timedelta(days=template.offset_days)
    if template.reference == "monthly":
        month_anchor = _monthly_anchor_date(employee, now_date)
        return month_anchor + timedelta(days=template.offset_days)
    return parse_date(employee.start_date) + timedelta(days=template.offset_days)


def _monthly_anchor_date(employee: Employee, now_date: date) -> date:
    month_start = now_date.replace(day=1)
    start_date = parse_date(employee.start_date)
    if start_date > month_start:
        return start_date.replace(day=1)
    return month_start


def task_should_remind(
    task: EmployeeTask,
    template: TaskTemplate,
    now_date: date,
    blocked: bool,
) -> bool:
    if task.completed:
        return False
    if blocked:
        return False
    due = parse_date(task.due_date) if task.due_date else now_date
    if now_date < due:
        return False
    if not task.last_reminder_sent:
        return True
    last_sent = parse_date(task.last_reminder_sent)
    if template.cadence.mode == "once":
        return False
    interval = template.cadence.interval_days
    if template.cadence.mode in {"daily", "custom"}:
        return (now_date - last_sent).days >= interval
    if template.cadence.mode == "weekly":
        return (now_date - last_sent).days >= 7 * interval
    if template.cadence.mode == "monthly":
        return now_date.month != last_sent.month or now_date.year != last_sent.year
    return (now_date - last_sent).days >= interval


def dependency_blocked(task: EmployeeTask, employee: Employee, template: TaskTemplate) -> bool:
    if not template.depends_on_incomplete:
        return False
    status_by_template = {item.template_id: item.completed for item in employee.tasks}
    for dependency_id in template.depends_on_incomplete:
        if not status_by_template.get(dependency_id, False):
            return False
    return True


def collect_due_reminders(
    employees: list[Employee],
    templates: list[TaskTemplate],
    now_date: date,
) -> list[ReminderItem]:
    reminders: list[ReminderItem] = []
    template_by_id = {template.id: template for template in templates}
    for employee in employees:
        for task in employee.tasks:
            template = template_by_id.get(task.template_id)
            if not template:
                continue
            blocked = dependency_blocked(task, employee, template)
            if not task_should_remind(task, template, now_date, blocked):
                continue
            reminders.append(
                ReminderItem(
                    employee_id=employee.id,
                    employee_name=employee.name,
                    task_id=task.id,
                    title=task.title,
                    due_date=task.due_date,
                )
            )
    return reminders


def apply_task_completion(task: EmployeeTask, completed: bool, now_date: date) -> None:
    task.completed = completed
    if completed:
        task.completed_at = to_date_str(now_date)
        return
    task.completed_at = None


def mark_reminder_sent(task: EmployeeTask, now_date: date) -> None:
    task.last_reminder_sent = to_date_str(now_date)
