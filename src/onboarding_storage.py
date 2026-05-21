from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any
from storage_utils import atomic_write_json, safe_read_json

from onboarding_migrations import backfill_onboarding_metadata
from onboarding_models import (
    Employee,
    EmailSettings,
    ReminderRunSummary,
    TaskTemplate,
    coerce_iso8601_timestamp,
)


logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES = [
    {
        "id": "quickbooks_access",
        "title": "Set up employee QuickBooks access",
        "reference": "acceptance_date",
        "offset_days": 7,
        "cadence": {"mode": "daily", "interval_days": 1},
    },
    {
        "id": "benefits_invite",
        "title": "Benefits invite",
        "reference": "acceptance_date",
        "offset_days": 1,
        "cadence": {"mode": "daily", "interval_days": 1},
        "deadline_label": "Within 24 hours of acceptance",
    },
    {
        "id": "generate_bio",
        "title": "Generate bio",
        "reference": "start_date",
        "offset_days": -1,
        "cadence": {"mode": "daily", "interval_days": 1},
    },
    {
        "id": "setup_email",
        "title": "Set up employee email",
        "reference": "start_date",
        "offset_days": -1,
        "cadence": {"mode": "daily", "interval_days": 1},
        "critical": True,
        "deadline_label": "Before day 1",
    },
    {
        "id": "website_bio_photo",
        "title": "Add website bio/photo",
        "reference": "monthly",
        "offset_days": 0,
        "cadence": {"mode": "monthly", "interval_days": 30},
    },
    {
        "id": "verification_letter",
        "title": "Provide verification of experience letter from Director",
        "reference": "start_date",
        "offset_days": 50,
        "cadence": {"mode": "daily", "interval_days": 1},
    },
    {
        "id": "permit_applied",
        "title": "Verify employee applied for permit",
        "reference": "start_date",
        "offset_days": 60,
        "cadence": {"mode": "daily", "interval_days": 1},
    },
    {
        "id": "live_scan",
        "title": "Verify employee was LiveScanned",
        "reference": "start_date",
        "offset_days": 60,
        "cadence": {"mode": "daily", "interval_days": 1},
    },
    {
        "id": "escalate_missing_compliance",
        "title": "Escalation: email director/owners if permit or LiveScan incomplete",
        "reference": "start_date",
        "offset_days": 90,
        "cadence": {"mode": "daily", "interval_days": 1},
        "depends_on_incomplete": ["permit_applied", "live_scan"],
    },
]


@dataclass(slots=True)
class AppState:
    employees: list[Employee]
    templates: list[TaskTemplate]
    email_settings: EmailSettings
    monthly_last_sent: str | None = None
    last_reminder_run_at: str | None = None
    reminder_run_history: list[dict[str, Any]] = field(default_factory=list)
    scheduler_settings: dict[str, Any] = field(default_factory=dict)
    scheduler_status: dict[str, Any] = field(default_factory=dict)


class JsonStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root_dir / "onboarding_data.json"
        self.settings_path = self.root_dir / "onboarding_settings.json"

    def load(self) -> AppState:
        state_data = self._load_json(self.state_path)
        settings_data = self._load_json(self.settings_path)
        state_data, migrated, summary = backfill_onboarding_metadata(state_data)
        if migrated:
            self._atomic_write_json(self.state_path, state_data)
            logger.info(
                "Onboarding metadata migration applied: templates_backfilled=%s tasks_backfilled=%s task_created_at_backfilled=%s",
                summary.templates_backfilled,
                summary.tasks_backfilled,
                summary.task_created_at_backfilled,
            )
        employees = [Employee.from_dict(item) for item in state_data.get("employees", [])]
        templates = [
            TaskTemplate.from_dict(item)
            for item in state_data.get("templates", DEFAULT_TEMPLATES)
        ]
        email_settings = EmailSettings.from_dict(settings_data.get("email", {}))
        monthly_last_sent = self._optional_str(state_data.get("monthly_last_sent"))
        last_reminder_run_at = coerce_iso8601_timestamp(state_data.get("last_reminder_run_at"))
        reminder_run_history = self._parse_reminder_history(state_data.get("reminder_run_history"))
        scheduler_settings = self._parse_dict_block(state_data.get("scheduler_settings"))
        scheduler_status = self._parse_dict_block(state_data.get("scheduler_status"))
        return AppState(
            employees=employees,
            templates=templates,
            email_settings=email_settings,
            monthly_last_sent=monthly_last_sent,
            last_reminder_run_at=last_reminder_run_at,
            reminder_run_history=reminder_run_history,
            scheduler_settings=scheduler_settings,
            scheduler_status=scheduler_status,
        )

    def save(self, state: AppState) -> None:
        self._atomic_write_json(
            self.state_path,
            {
                "employees": [employee.to_dict() for employee in state.employees],
                "templates": [template.to_dict() for template in state.templates],
                "monthly_last_sent": state.monthly_last_sent,
                "last_reminder_run_at": coerce_iso8601_timestamp(state.last_reminder_run_at),
                "reminder_run_history": self._parse_reminder_history(state.reminder_run_history),
                "scheduler_settings": self._parse_dict_block(state.scheduler_settings),
                "scheduler_status": self._parse_dict_block(state.scheduler_status),
            },
        )
        self._atomic_write_json(
            self.settings_path,
            {"email": state.email_settings.to_dict()},
        )

    @staticmethod
    def _load_json(path: Path) -> dict:
        return safe_read_json(path, default={}, expected_type=dict)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        atomic_write_json(path, payload, indent=2, ensure_ascii=False)

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _parse_dict_block(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _parse_reminder_history(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []

        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            items.append(ReminderRunSummary.from_dict(item).to_dict())
        return items
