from __future__ import annotations

import onboarding_operations
import onboarding_operations


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
