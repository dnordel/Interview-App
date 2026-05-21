from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CANONICAL_TEMPLATE_METADATA: dict[str, dict[str, Any]] = {
    "setup_email": {
        "critical": True,
        "deadline_label": "Before day 1",
    },
}


@dataclass(slots=True)
class OnboardingMigrationSummary:
    templates_backfilled: int = 0
    tasks_backfilled: int = 0
    task_created_at_backfilled: int = 0


def backfill_onboarding_metadata(state_data: dict[str, Any]) -> tuple[dict[str, Any], bool, OnboardingMigrationSummary]:
    summary = OnboardingMigrationSummary()
    changed = False

    templates = state_data.get("templates")
    if isinstance(templates, list):
        template_changed, template_count = _backfill_templates(templates)
        changed = changed or template_changed
        summary.templates_backfilled = template_count

    employees = state_data.get("employees")
    if isinstance(employees, list):
        task_changed, task_count = _backfill_tasks(employees)
        created_changed, created_count = _backfill_task_created_at(employees)
        changed = changed or task_changed or created_changed
        summary.tasks_backfilled = task_count
        summary.task_created_at_backfilled = created_count

    return state_data, changed, summary


def _backfill_templates(templates: list[Any]) -> tuple[bool, int]:
    changed = False
    backfilled = 0

    for template in templates:
        if not isinstance(template, dict):
            continue
        metadata = CANONICAL_TEMPLATE_METADATA.get(template.get("id"))
        if not metadata:
            continue
        if _apply_missing_metadata(template, metadata):
            changed = True
            backfilled += 1

    return changed, backfilled


def _backfill_tasks(employees: list[Any]) -> tuple[bool, int]:
    changed = False
    backfilled = 0

    for employee in employees:
        if not isinstance(employee, dict):
            continue
        tasks = employee.get("tasks")
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            metadata = CANONICAL_TEMPLATE_METADATA.get(task.get("template_id"))
            if not metadata:
                continue
            if _apply_missing_metadata(task, metadata):
                changed = True
                backfilled += 1

    return changed, backfilled




def _backfill_task_created_at(employees: list[Any]) -> tuple[bool, int]:
    changed = False
    backfilled = 0

    for employee in employees:
        if not isinstance(employee, dict):
            continue
        tasks = employee.get("tasks")
        if not isinstance(tasks, list):
            continue

        for task in tasks:
            if not isinstance(task, dict):
                continue
            created_at = task.get("created_at")
            if isinstance(created_at, str) and created_at.strip():
                continue

            due_date = task.get("due_date")
            completed_at = task.get("completed_at")
            if isinstance(due_date, str) and due_date.strip():
                task["created_at"] = due_date
            elif isinstance(completed_at, str) and completed_at.strip():
                task["created_at"] = completed_at
            else:
                task["created_at"] = None

            changed = True
            backfilled += 1

    return changed, backfilled

def _apply_missing_metadata(item: dict[str, Any], metadata: dict[str, Any]) -> bool:
    changed = False

    if _is_missing_bool(item.get("critical"), "critical" in item):
        item["critical"] = bool(metadata.get("critical", False))
        changed = True

    if _is_missing_text(item.get("deadline_label")):
        item["deadline_label"] = metadata.get("deadline_label")
        changed = True

    return changed


def _is_missing_bool(value: Any, has_key: bool) -> bool:
    if not has_key:
        return True
    return value is None or value == ""


def _is_missing_text(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return value.strip() == ""
