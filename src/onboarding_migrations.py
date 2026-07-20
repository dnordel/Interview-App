from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from onboarding_service import OnboardingService

from onboarding_operations import (
    CANONICAL_TEMPLATE_METADATA,
    OnboardingMigrationSummary,
    _apply_missing_metadata,
    _backfill_task_created_at,
    _backfill_tasks,
    _backfill_templates,
    _is_missing_bool,
    _is_missing_text,
    backfill_onboarding_metadata,
)

__all__ = [
    "CANONICAL_TEMPLATE_METADATA",
    "OnboardingMigrationSummary",
    "_apply_missing_metadata",
    "_backfill_task_created_at",
    "_backfill_tasks",
    "_backfill_templates",
    "_is_missing_bool",
    "_is_missing_text",
    "backfill_onboarding_metadata",
    "LegacyJsonMigrationPreview",
    "LegacyJsonMigrationResult",
    "preview_legacy_json_migration",
    "migrate_legacy_json_to_v2",
]


@dataclass(frozen=True)
class LegacyJsonMigrationPreview:
    source_path: Path
    source_sha256: str
    employee_count: int
    task_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class LegacyJsonMigrationResult:
    preview: LegacyJsonMigrationPreview
    backup_path: Path
    imported_employees: int
    imported_tasks: int


def preview_legacy_json_migration(source_path: Path) -> LegacyJsonMigrationPreview:
    source = Path(source_path).resolve(strict=True)
    if not source.is_file() or source.suffix.casefold() != ".json":
        raise ValueError("Legacy onboarding source must be a JSON file.")
    if source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Legacy onboarding JSON exceeds the 100 MB migration limit.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Legacy onboarding JSON is invalid.") from exc
    employees = payload.get("employees") if isinstance(payload, dict) else None
    if not isinstance(employees, list):
        raise ValueError("Legacy onboarding JSON must contain an employees list.")
    task_count = 0
    warnings: list[str] = []
    for index, employee in enumerate(employees, start=1):
        if not isinstance(employee, dict):
            raise ValueError(f"Legacy employee {index} must be an object.")
        for field_name in ("id", "name", "school", "acceptance_date", "start_date"):
            if not str(employee.get(field_name) or "").strip():
                raise ValueError(f"Legacy employee {index} is missing {field_name}.")
        tasks = employee.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError(f"Legacy employee {index} tasks must be a list.")
        for task_index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict) or not str(task.get("id") or "").strip() or not str(task.get("title") or "").strip():
                raise ValueError(f"Legacy employee {index} task {task_index} is invalid.")
            if not str(task.get("due_date") or "").strip():
                warnings.append(f"Employee {index} task {task_index} will use employee start date.")
        task_count += len(tasks)
    return LegacyJsonMigrationPreview(
        source_path=source,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        employee_count=len(employees),
        task_count=task_count,
        warnings=tuple(warnings),
    )


def migrate_legacy_json_to_v2(
    source_path: Path,
    *,
    service: OnboardingService,
    backup_dir: Path,
    confirmed: bool,
    expected_sha256: str = "",
) -> LegacyJsonMigrationResult:
    if service.access.role != "admin":
        raise PermissionError("Legacy onboarding migration is admin-only.")
    preview = preview_legacy_json_migration(source_path)
    expected_digest = str(expected_sha256 or "").strip().casefold()
    if expected_digest and preview.source_sha256.casefold() != expected_digest:
        raise ValueError("Legacy onboarding JSON changed after preview.")
    if not confirmed:
        raise ValueError("Legacy onboarding migration requires explicit confirmation.")
    backup_root = Path(backup_dir).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"onboarding_data.{preview.source_sha256[:12]}.backup.json"
    if not backup_path.exists():
        temporary = backup_root / f".{backup_path.name}.{uuid4().hex}.tmp"
        try:
            shutil.copy2(preview.source_path, temporary)
            with temporary.open("r+b") as file:
                os.fsync(file.fileno())
            temporary.replace(backup_path)
        finally:
            temporary.unlink(missing_ok=True)
    payload = json.loads(preview.source_path.read_text(encoding="utf-8"))
    imported_employees = 0
    imported_tasks = 0
    for legacy_employee in payload["employees"]:
        legacy_id = str(legacy_employee["id"]).strip()
        source_key = f"legacy-onboarding:{legacy_id}"
        employee = next(
            (item for item in service.list_employees() if item.source_history_id == source_key),
            None,
        )
        if employee is None:
            employee = service.create_employee(
                legal_name=str(legacy_employee["name"]),
                school=str(legacy_employee["school"]),
                role="Legacy Employee",
                acceptance_date=str(legacy_employee["acceptance_date"]),
                start_date=str(legacy_employee["start_date"]),
                source_history_id=source_key,
            )
            imported_employees += 1
        existing_keys = {
            task.template_key for task in service.list_tasks() if task.employee_id == employee.id
        }
        for legacy_task in legacy_employee.get("tasks", []):
            task_key = f"legacy:{str(legacy_task['id']).strip()}"
            if task_key in existing_keys:
                continue
            task = service.create_task(
                employee_id=employee.id,
                title=str(legacy_task["title"]),
                owner_role="Admin",
                due_date=str(legacy_task.get("due_date") or employee.start_date),
                critical=bool(legacy_task.get("critical", False)),
                template_key=task_key,
                template_version=1,
            )
            if bool(legacy_task.get("completed", False)):
                service.complete_task(task.id)
            imported_tasks += 1
            existing_keys.add(task_key)
    return LegacyJsonMigrationResult(
        preview=preview,
        backup_path=backup_path,
        imported_employees=imported_employees,
        imported_tasks=imported_tasks,
    )
