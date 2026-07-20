from __future__ import annotations

import onboarding_operations
import onboarding_operations
import json

import pytest

from onboarding_migrations import migrate_legacy_json_to_v2, preview_legacy_json_migration
from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import OnboardingStore


def test_backfill_onboarding_metadata_updates_templates_tasks_and_created_at():
    state_data = {
        "templates": [{"id": "setup_email", "critical": "", "deadline_label": ""}],
        "employees": [
            {
                "tasks": [
                    {
                        "template_id": "setup_email",
                        "critical": None,
                        "deadline_label": "",
                        "due_date": "2026-01-10",
                    },
                    {
                        "template_id": "other",
                        "completed_at": "2026-01-11",
                    },
                ]
            }
        ],
    }

    migrated, changed, summary = onboarding_operations.backfill_onboarding_metadata(state_data)

    assert migrated is state_data
    assert changed is True
    assert summary.templates_backfilled == 1
    assert summary.tasks_backfilled == 1
    assert summary.task_created_at_backfilled == 2
    assert state_data["templates"][0]["critical"] is True
    assert state_data["templates"][0]["deadline_label"] == "Before day 1"
    assert state_data["employees"][0]["tasks"][0]["created_at"] == "2026-01-10"
    assert state_data["employees"][0]["tasks"][1]["created_at"] == "2026-01-11"


def test_backfill_onboarding_metadata_noops_when_data_already_complete():
    state_data = {
        "templates": [{"id": "setup_email", "critical": False, "deadline_label": "Custom"}],
        "employees": [
            {
                "tasks": [
                    {
                        "template_id": "setup_email",
                        "critical": False,
                        "deadline_label": "Custom",
                        "created_at": "2026-01-01",
                    }
                ]
            }
        ],
    }

    _migrated, changed, summary = onboarding_operations.backfill_onboarding_metadata(state_data)

    assert changed is False
    assert summary == onboarding_operations.OnboardingMigrationSummary()


def test_legacy_json_migration_requires_preview_backup_confirmation_and_is_idempotent(tmp_path):
    legacy = tmp_path / "onboarding_data.json"
    legacy.write_text(
        json.dumps(
            {
                "employees": [
                    {
                        "id": "legacy-employee-1",
                        "name": "Jordan Lee",
                        "school": "Palmdale",
                        "acceptance_date": "2026-07-01",
                        "start_date": "2026-07-15",
                        "tasks": [
                            {
                                "id": "legacy-task-1",
                                "title": "Orientation",
                                "due_date": "2026-07-15",
                                "completed": True,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "v2.sqlite3"),
        OnboardingAccess(role="admin", actor="migration"),
    )
    preview = preview_legacy_json_migration(legacy)
    assert preview.employee_count == 1 and preview.task_count == 1
    with pytest.raises(ValueError, match="confirmation"):
        migrate_legacy_json_to_v2(legacy, service=service, backup_dir=tmp_path / "backups", confirmed=False)

    first = migrate_legacy_json_to_v2(
        legacy,
        service=service,
        backup_dir=tmp_path / "backups",
        confirmed=True,
    )
    second = migrate_legacy_json_to_v2(
        legacy,
        service=service,
        backup_dir=tmp_path / "backups",
        confirmed=True,
    )
    assert first.imported_employees == 1 and first.imported_tasks == 1
    assert second.imported_employees == 0 and second.imported_tasks == 0
    assert first.backup_path.is_file()
    task = service.list_tasks()[0]
    assert task.owner_role == "Admin"
    assert task.status == "completed"


def test_legacy_json_import_rejects_source_changed_after_preview(tmp_path):
    legacy = tmp_path / "onboarding_data.json"
    legacy.write_text(json.dumps({"employees": []}), encoding="utf-8")
    preview = preview_legacy_json_migration(legacy)
    legacy.write_text(json.dumps({"employees": []}, indent=2), encoding="utf-8")
    service = OnboardingService(
        OnboardingStore(tmp_path / "v2.sqlite3"),
        OnboardingAccess(role="admin", actor="migration"),
    )

    with pytest.raises(ValueError, match="changed after preview"):
        migrate_legacy_json_to_v2(
            legacy,
            service=service,
            backup_dir=tmp_path / "backups",
            confirmed=True,
            expected_sha256=preview.source_sha256,
        )
