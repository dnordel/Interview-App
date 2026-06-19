from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from importlib import import_module
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import ssl
import tempfile
from types import ModuleType
import tkinter as tk
from tkinter import ttk
from typing import Any, Literal, Mapping
import uuid

from platform_services import atomic_write_json, safe_read_json
from scoring_reporting import (
    missing_placeholder_keys,
    render_template,
    sanitize_email_subject,
    sender_email_error_reason,
)
from platform_services import UxMetricsLogger


logger = logging.getLogger(__name__)

DATE_FMT = "%Y-%m-%d"
SMTP_PASSWORD_ENV_KEYS = ("ONBOARDING_SMTP_PASSWORD", "SMTP_PASSWORD")
ActionEmphasis = Literal["primary", "secondary"]
SCHEDULER_RESULT_VALUES = {"success", "failed", "dry_run", "no_due_items"}
SPECIFIC_DATE_REFERENCE_PREFIX = "date:"
DEFAULT_CRITICAL_WINDOW_DAYS = 3
DEFAULT_EXPECTED_INTERVAL_HOURS = 24
_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def resolve_smtp_password(stored_password: str) -> str:
    for key in SMTP_PASSWORD_ENV_KEYS:
        value = str(os.environ.get(key, "")).strip()
        if value:
            return value
    return str(stored_password or "")


def today_local() -> date:
    return date.today()


def parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FMT).date()


def to_date_str(value: date | None) -> str | None:
    if not value:
        return None
    return value.strftime(DATE_FMT)


def parse_iso8601_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def format_iso8601_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


def coerce_iso8601_timestamp(value: Any) -> str | None:
    parsed = parse_iso8601_datetime(value)
    if not parsed:
        return None
    return format_iso8601_datetime(parsed)


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _safe_int(value: Any, default: int, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        return max(minimum, parsed)
    return parsed


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

KPI_FILTER_ENTRYPOINTS: dict[str, str] = {
    "overdue": "overdue",
    "due_today": "due_today",
    "urgent": "urgent",
    "critical": "urgent",
    "pending": "pending",
}

CANONICAL_TEMPLATE_METADATA: dict[str, dict[str, Any]] = {
    "setup_email": {
        "critical": True,
        "deadline_label": "Before day 1",
    },
}

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
class ReminderCadence:
    mode: str = "daily"
    interval_days: int = 1

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReminderCadence":
        source = payload or {}
        mode = source.get("mode", "daily")
        interval = _safe_int(source.get("interval_days", 1), default=1, minimum=1)
        return cls(mode=mode, interval_days=interval)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "interval_days": self.interval_days,
        }


@dataclass(slots=True)
class TaskTemplate:
    id: str
    title: str
    reference: str
    offset_days: int = 0
    cadence: ReminderCadence = field(default_factory=ReminderCadence)
    depends_on_incomplete: list[str] = field(default_factory=list)
    critical: bool = False
    deadline_label: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskTemplate":
        return cls(
            id=payload["id"],
            title=payload["title"],
            reference=payload.get("reference", "start_date"),
            offset_days=int(payload.get("offset_days", 0)),
            cadence=ReminderCadence.from_dict(payload.get("cadence")),
            depends_on_incomplete=list(payload.get("depends_on_incomplete", [])),
            critical=bool(payload.get("critical", False)),
            deadline_label=payload.get("deadline_label") or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "reference": self.reference,
            "offset_days": self.offset_days,
            "cadence": self.cadence.to_dict(),
            "depends_on_incomplete": self.depends_on_incomplete,
            "critical": self.critical,
            "deadline_label": self.deadline_label,
        }


@dataclass(slots=True)
class EmployeeTask:
    id: str
    template_id: str
    title: str
    created_at: str | None = None
    due_date: str | None = None
    completed: bool = False
    completed_at: str | None = None
    last_reminder_sent: str | None = None
    critical: bool = False
    deadline_label: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EmployeeTask":
        return cls(
            id=payload["id"],
            template_id=payload.get("template_id", ""),
            title=payload["title"],
            created_at=payload.get("created_at"),
            due_date=payload.get("due_date"),
            completed=bool(payload.get("completed", False)),
            completed_at=payload.get("completed_at"),
            last_reminder_sent=payload.get("last_reminder_sent"),
            critical=bool(payload.get("critical", False)),
            deadline_label=payload.get("deadline_label") or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "title": self.title,
            "created_at": self.created_at,
            "due_date": self.due_date,
            "completed": self.completed,
            "completed_at": self.completed_at,
            "last_reminder_sent": self.last_reminder_sent,
            "critical": self.critical,
            "deadline_label": self.deadline_label,
        }


@dataclass(slots=True)
class Employee:
    id: str
    name: str
    acceptance_date: str
    start_date: str
    school: str = ""
    tasks: list[EmployeeTask] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Employee":
        tasks = [EmployeeTask.from_dict(item) for item in payload.get("tasks", [])]
        return cls(
            id=payload["id"],
            name=payload["name"],
            acceptance_date=payload["acceptance_date"],
            start_date=payload["start_date"],
            school=str(payload.get("school", "")).strip(),
            tasks=tasks,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "acceptance_date": self.acceptance_date,
            "start_date": self.start_date,
            "school": self.school,
            "tasks": [task.to_dict() for task in self.tasks],
        }


@dataclass(slots=True)
class LaunchEmployeeSeed:
    name: str = ""
    school: str = ""
    acceptance_date: str = ""
    start_date: str = ""

    @classmethod
    def from_dict(cls, payload: Any) -> "LaunchEmployeeSeed":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            name=str(payload.get("name") or "").strip(),
            school=str(payload.get("school") or "").strip(),
            acceptance_date=str(payload.get("acceptance_date") or "").strip(),
            start_date=str(payload.get("start_date") or "").strip(),
        )

    def has_prefill(self) -> bool:
        return any((self.name, self.school, self.acceptance_date, self.start_date))

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "school": self.school,
            "acceptance_date": self.acceptance_date,
            "start_date": self.start_date,
        }


@dataclass(slots=True)
class EmailSettings:
    sender_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    use_tls: bool = True
    imap_or_pop_host: str = ""
    imap_or_pop_port: int = 993
    director_and_owners: str = ""
    reminder_recipients: str = ""
    reminder_subject_template: str = ""
    reminder_body_template: str = ""
    escalation_subject_template: str = ""
    escalation_body_template: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EmailSettings":
        source = payload or {}
        return cls(
            sender_email=source.get("sender_email", ""),
            smtp_host=source.get("smtp_host", ""),
            smtp_port=_safe_int(source.get("smtp_port", 587), default=587, minimum=1),
            smtp_username=source.get("smtp_username", ""),
            smtp_password=source.get("smtp_password", ""),
            use_tls=bool(source.get("use_tls", True)),
            imap_or_pop_host=source.get("imap_or_pop_host", ""),
            imap_or_pop_port=_safe_int(source.get("imap_or_pop_port", 993), default=993, minimum=1),
            director_and_owners=source.get("director_and_owners", ""),
            reminder_recipients=source.get("reminder_recipients", ""),
            reminder_subject_template=source.get("reminder_subject_template", ""),
            reminder_body_template=source.get("reminder_body_template", ""),
            escalation_subject_template=source.get("escalation_subject_template", ""),
            escalation_body_template=source.get("escalation_body_template", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_email": self.sender_email,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_username": self.smtp_username,
            "smtp_password": self.smtp_password,
            "use_tls": self.use_tls,
            "imap_or_pop_host": self.imap_or_pop_host,
            "imap_or_pop_port": self.imap_or_pop_port,
            "director_and_owners": self.director_and_owners,
            "reminder_recipients": self.reminder_recipients,
            "reminder_subject_template": self.reminder_subject_template,
            "reminder_body_template": self.reminder_body_template,
            "escalation_subject_template": self.escalation_subject_template,
            "escalation_body_template": self.escalation_body_template,
        }


@dataclass(slots=True)
class ReminderTaskOutcome:
    employee_id: str = ""
    employee_name: str = ""
    task_id: str = ""
    title: str = ""
    due_date: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReminderTaskOutcome":
        source = payload or {}
        return cls(
            employee_id=str(source.get("employee_id") or ""),
            employee_name=str(source.get("employee_name") or ""),
            task_id=str(source.get("task_id") or ""),
            title=str(source.get("title") or ""),
            due_date=source.get("due_date") if isinstance(source.get("due_date"), str) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "task_id": self.task_id,
            "title": self.title,
            "due_date": self.due_date,
        }


@dataclass(slots=True)
class ReminderRecipientOutcome:
    phase: str = ""
    attempted: bool = False
    success: bool = False
    recipients: list[str] = field(default_factory=list)
    item_count: int = 0
    message: str = ""
    error: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReminderRecipientOutcome":
        source = payload or {}
        raw_recipients = source.get("recipients")
        recipients = [str(item) for item in raw_recipients] if isinstance(raw_recipients, list) else []
        return cls(
            phase=str(source.get("phase") or ""),
            attempted=bool(source.get("attempted", False)),
            success=bool(source.get("success", False)),
            recipients=recipients,
            item_count=_safe_int(source.get("item_count", 0), default=0, minimum=0),
            message=str(source.get("message") or ""),
            error=str(source.get("error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "attempted": self.attempted,
            "success": self.success,
            "recipients": self.recipients,
            "item_count": self.item_count,
            "message": self.message,
            "error": self.error,
        }


@dataclass(slots=True)
class ReminderRunSummary:
    run_id: str = ""
    ran_at: str | None = None
    dry_run: bool = False
    recipients: dict[str, list[str]] = field(default_factory=dict)
    tasks: list[ReminderTaskOutcome] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    outcomes: list[ReminderRecipientOutcome] = field(default_factory=list)
    task_breakdown: dict[str, list[ReminderTaskOutcome]] = field(default_factory=dict)
    escalation_candidates: list[ReminderTaskOutcome] = field(default_factory=list)
    channel_results: dict[str, dict[str, int]] = field(default_factory=dict)
    error_summaries: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReminderRunSummary":
        source = payload or {}
        raw_recipients = source.get("recipients")
        recipients: dict[str, list[str]] = {}
        if isinstance(raw_recipients, dict):
            for key, value in raw_recipients.items():
                if isinstance(value, list):
                    recipients[str(key)] = [str(item) for item in value]

        raw_counts = source.get("counts")
        counts: dict[str, int] = {}
        if isinstance(raw_counts, dict):
            for key, value in raw_counts.items():
                counts[str(key)] = _safe_int(value, default=0, minimum=0)

        raw_tasks = source.get("tasks")
        tasks = [ReminderTaskOutcome.from_dict(item) for item in raw_tasks] if isinstance(raw_tasks, list) else []

        raw_outcomes = source.get("outcomes")
        outcomes = [ReminderRecipientOutcome.from_dict(item) for item in raw_outcomes] if isinstance(raw_outcomes, list) else []

        raw_breakdown = source.get("task_breakdown")
        task_breakdown: dict[str, list[ReminderTaskOutcome]] = {}
        if isinstance(raw_breakdown, dict):
            for key, value in raw_breakdown.items():
                if isinstance(value, list):
                    task_breakdown[str(key)] = [ReminderTaskOutcome.from_dict(item) for item in value]

        raw_escalation = source.get("escalation_candidates")
        escalation_candidates = [ReminderTaskOutcome.from_dict(item) for item in raw_escalation] if isinstance(raw_escalation, list) else []

        raw_channels = source.get("channel_results")
        channel_results: dict[str, dict[str, int]] = {}
        if isinstance(raw_channels, dict):
            for channel, block in raw_channels.items():
                if not isinstance(block, dict):
                    continue
                channel_results[str(channel)] = {
                    "attempted": _safe_int(block.get("attempted", 0), default=0, minimum=0),
                    "sent": _safe_int(block.get("sent", 0), default=0, minimum=0),
                    "failed": _safe_int(block.get("failed", 0), default=0, minimum=0),
                }

        raw_error_summaries = source.get("error_summaries")
        error_summaries = [str(item) for item in raw_error_summaries] if isinstance(raw_error_summaries, list) else []

        return cls(
            run_id=str(source.get("run_id") or ""),
            ran_at=coerce_iso8601_timestamp(source.get("ran_at")),
            dry_run=bool(source.get("dry_run", False)),
            recipients=recipients,
            tasks=tasks,
            counts=counts,
            outcomes=outcomes,
            task_breakdown=task_breakdown,
            escalation_candidates=escalation_candidates,
            channel_results=channel_results,
            error_summaries=error_summaries,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ran_at": self.ran_at,
            "dry_run": self.dry_run,
            "recipients": self.recipients,
            "tasks": [task.to_dict() for task in self.tasks],
            "counts": self.counts,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "task_breakdown": {
                key: [task.to_dict() for task in value]
                for key, value in self.task_breakdown.items()
            },
            "escalation_candidates": [task.to_dict() for task in self.escalation_candidates],
            "channel_results": self.channel_results,
            "error_summaries": self.error_summaries,
        }


@dataclass(frozen=True)
class ActionItemSpec:
    label: str
    command_name: str
    emphasis: ActionEmphasis
    metrics_key: str | None = None
    helper_text: str = ""
    shortcut_hint: str = ""


@dataclass(frozen=True)
class ActionSectionSpec:
    title: str
    helper_text: str
    actions: tuple[ActionItemSpec, ...]


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


@dataclass(frozen=True, slots=True)
class TaskFilterOption:
    key: str
    label: str
    hotkey: str


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


@dataclass(slots=True)
class ReminderHealthStatus:
    severity: str
    message: str
    hours_or_days_late: int | None


@dataclass(slots=True)
class ReminderItem:
    employee_id: str
    employee_name: str
    task_id: str
    title: str
    due_date: str | None


@dataclass(slots=True)
class OnboardingMigrationSummary:
    templates_backfilled: int = 0
    tasks_backfilled: int = 0
    task_created_at_backfilled: int = 0


@dataclass(slots=True)
class SendOutcome:
    phase: str
    attempted: bool
    success: bool
    recipients: list[str]
    item_count: int
    message: str = ""
    error: str = ""


@dataclass(slots=True)
class ReminderRunResult:
    run_id: str
    ran_at: str
    dry_run: bool
    recipients: dict[str, list[str]]
    tasks: list[dict[str, str | None]]
    counts: dict[str, int]
    outcomes: list[SendOutcome]
    task_breakdown: dict[str, list[dict[str, str | None]]]
    escalation_candidates: list[dict[str, str | None]]
    channel_results: dict[str, dict[str, int]]
    error_summaries: list[str]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["outcomes"] = [asdict(outcome) for outcome in self.outcomes]
        return payload


@dataclass(slots=True)
class ReminderRunContext:
    run_id: str
    now_date: date
    reminders: list[ReminderItem]
    monthly_lines: list[str]
    escalation_lines: list[str]
    recipients: dict[str, list[str]]
    school: str
    runtime_values: dict[str, str]


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


@dataclass(slots=True)
class ScrollableCanvasArea:
    shell: ttk.Frame
    canvas: tk.Canvas
    interior: ttk.Frame
    scrollbar: ttk.Scrollbar
    window_id: int


@dataclass(slots=True)
class ScrollableModalContainer:
    dialog: tk.Toplevel
    canvas: tk.Canvas
    interior: ttk.Frame
    button_bar: ttk.Frame
    scrollbar: ttk.Scrollbar


TASK_FILTER_OPTIONS = [
    TaskFilterOption(key="all", label="All", hotkey="1"),
    TaskFilterOption(key="pending", label="Pending", hotkey="2"),
    TaskFilterOption(key="urgent", label="Urgent", hotkey="3"),
    TaskFilterOption(key="overdue", label="Overdue", hotkey="4"),
    TaskFilterOption(key="due_today", label="Due Today", hotkey="5"),
    TaskFilterOption(key="due_soon", label="Due in 3 Days", hotkey="6"),
    TaskFilterOption(key="completed", label="Completed", hotkey="7"),
]


def onboarding_action_sections() -> tuple[ActionSectionSpec, ...]:
    return (
        ActionSectionSpec(
            title="Daily workflow",
            helper_text="Start here each day to execute the most time-sensitive reminders.",
            actions=(
                ActionItemSpec(
                    label="Run Reminders Now",
                    command_name="_on_primary_reminder_cta_click",
                    emphasis="primary",
                    helper_text="Sends due reminders and escalations using current settings.",
                    shortcut_hint="Tab to focus, Enter/Space to run",
                ),
                ActionItemSpec(
                    label="Run Reminders (Dry Run)",
                    command_name="_run_reminders_dry_run_from_ui",
                    emphasis="secondary",
                    metrics_key="run_reminders_dry_run",
                    helper_text="Preview recipients and message content without sending.",
                    shortcut_hint="Tab to focus, Enter/Space to preview",
                ),
            ),
        ),
        ActionSectionSpec(
            title="Candidate management",
            helper_text="Create and maintain onboarding records and task templates.",
            actions=(
                ActionItemSpec(
                    label="Add Employee",
                    command_name="open_add_employee_dialog",
                    emphasis="secondary",
                    metrics_key="add_employee",
                    helper_text="Create a new onboarding plan for a newly hired teacher.",
                ),
                ActionItemSpec(
                    label="Add Custom Template",
                    command_name="open_custom_template_dialog",
                    emphasis="secondary",
                    metrics_key="add_custom_template",
                    helper_text="Save reusable onboarding task sets for future hires.",
                ),
            ),
        ),
        ActionSectionSpec(
            title="Communications",
            helper_text="Configure outbound email behavior used by reminder automation.",
            actions=(
                ActionItemSpec(
                    label="Email Settings",
                    command_name="open_email_settings",
                    emphasis="secondary",
                    metrics_key="open_email_settings",
                    helper_text="Update sender identity, recipients, and reminder templates.",
                ),
            ),
        ),
        ActionSectionSpec(
            title="Admin & advanced",
            helper_text="Less frequent settings for storage and environment setup.",
            actions=(
                ActionItemSpec(
                    label="Use Dropbox Folder",
                    command_name="change_storage_folder",
                    emphasis="secondary",
                    metrics_key="change_storage_folder",
                    helper_text="Change where onboarding data files are stored.",
                ),
            ),
        ),
    )


def build_scheduler_status(summary: Any, scheduler_enabled: bool, run_source: str = "manual") -> dict[str, Any]:
    status: dict[str, Any] = {
        "enabled": bool(scheduler_enabled),
        "last_scheduler_run_at": summary.ran_at,
        "last_scheduler_result": _derive_result(summary),
    }
    last_error = _derive_last_error(summary)
    if last_error:
        status["last_error"] = last_error
    status["last_run_source"] = "scheduler" if run_source == "scheduler" else "manual"
    return status


def normalize_run_source(value: str | None) -> str:
    if value == "scheduler":
        return "scheduler"
    return "manual"


def _derive_result(summary: Any) -> str:
    due_count = summary.counts.get("due_reminders", 0)
    if summary.dry_run:
        return "dry_run"
    if due_count == 0:
        return "no_due_items"

    has_outcomes = bool(summary.outcomes)
    has_failure = any(not outcome.success for outcome in summary.outcomes)
    has_success = any(outcome.success for outcome in summary.outcomes)

    if has_failure:
        return "failed"
    if has_outcomes and has_success:
        return "success"
    return "failed"


def _derive_last_error(summary: Any) -> str:
    for outcome in summary.outcomes:
        message = outcome.error.strip()
        if message:
            return message
    return ""


def build_specific_date_reference(value: date) -> str:
    return f"{SPECIFIC_DATE_REFERENCE_PREFIX}{value.isoformat()}"


def parse_specific_date_reference(reference: str) -> date | None:
    normalized = str(reference or "").strip()
    if not normalized.startswith(SPECIFIC_DATE_REFERENCE_PREFIX):
        return None

    date_part = normalized.removeprefix(SPECIFIC_DATE_REFERENCE_PREFIX).strip()
    if not date_part:
        return None

    try:
        return datetime.strptime(date_part, "%Y-%m-%d").date()
    except ValueError:
        return None


def task_status_badge_text(status: str) -> str:
    icon = TASK_STATUS_ICONS.get(status, "•")
    label = TASK_STATUS_LABELS.get(status, status.title())
    return f"{icon} {label}"


def task_status(task: Any, today: date) -> str:
    if task.completed:
        return "completed"

    if not task.due_date:
        return "unscheduled"

    due_date = _parse_onboarding_date(task.due_date)
    if due_date < today:
        return "overdue"

    if due_date == today:
        return "due_today"

    if (due_date - today).days <= 3:
        return "due_soon"

    return "upcoming"


def sorted_tasks_for_display(tasks: list[Any], today: date) -> list[Any]:
    return sorted(tasks, key=lambda item: _task_sort_key(item, today))


def _task_sort_key(task: Any, today: date) -> tuple[int, date, str]:
    status = task_status(task, today)
    order = TASK_STATUS_ORDER[status]
    due = _parse_onboarding_date(task.due_date) if task.due_date else date.max
    return order, due, task.title.lower()


def build_onboarding_overview(employees: list[Any], today: date) -> OnboardingOverview:
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


def _summarize_employee_tasks(employee: Any, today: date) -> EmployeeTaskSummary:
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


def filtered_tasks(tasks: list[Any], today: date, filter_key: str) -> list[Any]:
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


def urgent_filter_result_count(tasks: list[Any], today: date) -> int:
    return len(filtered_tasks(tasks, today, "urgent"))


def task_display_sort_key(task: Any, today: date) -> tuple[int, date, str]:
    status = task_status(task, today)
    priority = _status_priority(status)
    due = _parse_onboarding_date(task.due_date) if task.due_date else date.max
    return priority, due, task.title.lower()


def format_due_date_short(due_date: str | None) -> str:
    if not due_date:
        return "—"
    return _parse_onboarding_date(due_date).strftime("%b %d")


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


def _parse_onboarding_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_dashboard_today_summary(
    history_rows: list[dict[str, Any]],
    employees: list[Any],
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
    employees: list[Any],
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


def build_dashboard_kpi_chips(
    summary: DashboardTodaySummary,
    employees: list[Any],
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
    employees: list[Any],
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


def first_matching_navigation(employees: list[Any], today: date, filter_key: str) -> dict[str, str] | None:
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
    employees: list[Any],
    today: date,
    kpi_key: str,
) -> dict[str, str] | None:
    filter_key = filter_for_dashboard_kpi(kpi_key)
    if filter_key is None:
        return None
    return first_matching_navigation(employees, today, filter_key)


def _count_tasks_for_filter(employees: list[Any], today: date, filter_key: str) -> int:
    count = 0
    for employee in employees:
        count += len(filtered_tasks(employee.tasks, today, filter_key))
    return count


def build_scrollable_canvas_area(
    parent: tk.Misc,
    *,
    interior_padding: int | tuple[int, int, int, int] = 0,
    canvas_kwargs: dict[str, object] | None = None,
) -> ScrollableCanvasArea:
    shell = ttk.Frame(parent)
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(0, weight=1)

    resolved_canvas_kwargs = {"highlightthickness": 0, "borderwidth": 0}
    if canvas_kwargs:
        resolved_canvas_kwargs.update(canvas_kwargs)

    canvas = tk.Canvas(shell, **resolved_canvas_kwargs)
    scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    interior = ttk.Frame(canvas, padding=interior_padding)
    window_id = canvas.create_window((0, 0), window=interior, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    interior.columnconfigure(0, weight=1)
    return ScrollableCanvasArea(
        shell=shell,
        canvas=canvas,
        interior=interior,
        scrollbar=scrollbar,
        window_id=window_id,
    )


def bind_canvas_mousewheel(
    canvas: tk.Canvas,
    *,
    activate_widgets: Sequence[tk.Misc],
    release_widgets: Sequence[tk.Misc] = (),
) -> None:
    def _scroll_units(event: tk.Event) -> int:
        delta = getattr(event, "delta", 0)
        if delta:
            return int(-delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        button_number = getattr(event, "num", None)
        if button_number == 4:
            return -1
        if button_number == 5:
            return 1
        return 0

    def _on_mousewheel(event: tk.Event) -> str | None:
        units = _scroll_units(event)
        if units == 0:
            return None
        canvas.yview_scroll(units, "units")
        return "break"

    def _bind_mousewheel(_event: tk.Event | None = None) -> None:
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

    def _unbind_mousewheel(_event: tk.Event | None = None) -> None:
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    for widget in activate_widgets:
        widget.bind("<Enter>", _bind_mousewheel, add="+")
        widget.bind("<Leave>", _unbind_mousewheel, add="+")
        widget.bind("<FocusIn>", _bind_mousewheel, add="+")
        widget.bind("<FocusOut>", _unbind_mousewheel, add="+")

    for widget in release_widgets:
        widget.bind("<Leave>", _unbind_mousewheel, add="+")
        widget.bind("<FocusOut>", _unbind_mousewheel, add="+")

    toplevel = canvas.winfo_toplevel()
    toplevel.bind(
        "<Destroy>",
        lambda event: _unbind_mousewheel(event) if event.widget is toplevel else None,
        add="+",
    )


def scroll_widget_into_view(canvas: tk.Canvas, widget: tk.Misc, *, padding: int = 12) -> None:
    bbox = canvas.bbox("all")
    if not bbox:
        return
    total_height = bbox[3] - bbox[1]
    if total_height <= 0:
        return

    canvas.update_idletasks()
    top = canvas.canvasy(0)
    bottom = top + canvas.winfo_height()
    widget_top = widget.winfo_rooty() - canvas.winfo_rooty() + top
    widget_bottom = widget_top + widget.winfo_height()

    if widget_top < top + padding:
        target = max(0, widget_top - padding)
        canvas.yview_moveto(target / total_height)
        return
    if widget_bottom <= bottom - padding:
        return
    target = min(total_height, widget_bottom - canvas.winfo_height() + padding)
    canvas.yview_moveto(target / total_height)


def build_scrollable_modal_container(
    parent: tk.Misc,
    *,
    title: str,
    body_padding: int | tuple[int, int, int, int] = 10,
    button_padding: tuple[int, int, int, int] = (10, 0, 10, 10),
) -> ScrollableModalContainer:
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(0, weight=1)

    scrollable_area = build_scrollable_canvas_area(
        dialog,
        interior_padding=body_padding,
        canvas_kwargs={"takefocus": 0},
    )
    shell = scrollable_area.shell
    shell.grid(row=0, column=0, sticky="nsew")
    canvas = scrollable_area.canvas
    scrollbar = scrollable_area.scrollbar
    interior = scrollable_area.interior
    window_id = scrollable_area.window_id

    button_bar = ttk.Frame(shell, padding=button_padding)
    button_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
    button_bar.columnconfigure(0, weight=1)

    def _update_scroll_region(_event: tk.Event | None = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _match_interior_width(event: tk.Event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    interior.bind("<Configure>", _update_scroll_region)
    canvas.bind("<Configure>", _match_interior_width)
    bind_canvas_mousewheel(canvas, activate_widgets=(canvas, interior))

    _update_scroll_region()
    return ScrollableModalContainer(
        dialog=dialog,
        canvas=canvas,
        interior=interior,
        button_bar=button_bar,
        scrollbar=scrollbar,
    )


def build_launch_context(
    *,
    employee_id: str | None = None,
    urgent_only: bool = False,
    employee_seed: LaunchEmployeeSeed | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "employee_id": str(employee_id or "").strip(),
        "urgent_only": bool(urgent_only),
    }
    if employee_seed is None:
        return context
    if not employee_seed.has_prefill():
        return context
    context["employee_seed"] = employee_seed.to_dict()
    return context


def write_launch_context_file(payload: dict[str, object]) -> Path | None:
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="onboarding_launch_",
            delete=False,
            encoding="utf-8",
        ) as handle:
            json.dump(payload, handle)
            return Path(handle.name)
    except OSError:
        return None


def read_launch_context_file(path_value: str | None) -> dict[str, object]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return dict(payload)


def extract_employee_seed(payload: dict[str, object] | None) -> LaunchEmployeeSeed | None:
    if not isinstance(payload, dict):
        return None
    seed = LaunchEmployeeSeed.from_dict(payload.get("employee_seed"))
    if not seed.has_prefill():
        return None
    return seed


def scheduler_opt_in(settings: dict[str, Any]) -> bool:
    if "enabled" in settings:
        return bool(settings.get("enabled"))
    return bool(settings.get("opt_in", False))


def scheduler_expected_interval_hours(settings: dict[str, Any]) -> int:
    if "expected_interval_hours" in settings:
        return _positive_int(settings.get("expected_interval_hours"), DEFAULT_EXPECTED_INTERVAL_HOURS)

    if "run_interval_minutes" in settings:
        minutes = _positive_int(settings.get("run_interval_minutes"), DEFAULT_EXPECTED_INTERVAL_HOURS * 60)
        return max(1, minutes // 60)

    return DEFAULT_EXPECTED_INTERVAL_HOURS


def scheduler_status_text(status: dict[str, Any]) -> str:
    if not status:
        return "No scheduler-triggered run status recorded yet."

    result = status.get("last_scheduler_result")
    if not isinstance(result, str) or not result.strip():
        result = status.get("last_result")

    if isinstance(result, str) and result.strip():
        return result.strip()

    error = status.get("last_error")
    if isinstance(error, str) and error.strip():
        return f"Error: {error.strip()}"

    message = status.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return "Scheduler status is available but no result text was provided."


def scheduler_script_path(script_path: str | Path | None = None) -> str:
    candidate = Path(script_path) if script_path else Path(__file__).resolve().with_name("onboarding_app.pyw")
    return str(candidate)


def scheduler_command_example(script_path: str | Path | None = None) -> str:
    script = scheduler_script_path(script_path)
    return f'schtasks /Create /SC HOURLY /MO 1 /TN "OnboardingReminderRunner" /TR "pythonw \\\"{script}\\\" --run-reminders" /F'


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def split_and_validate_recipients(raw: str) -> tuple[list[str], list[str]]:
    entries = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    valid = [item for item in entries if _EMAIL_PATTERN.match(item)]
    invalid = [item for item in entries if not _EMAIL_PATTERN.match(item)]
    return valid, invalid


def recipient_warning_text(raw: str, *, channel_label: str) -> str:
    valid, invalid = split_and_validate_recipients(raw)
    if not valid and not invalid:
        return f"{channel_label}: Add at least one recipient email address before running reminders."
    if invalid:
        return f"{channel_label}: Fix malformed addresses: {', '.join(invalid)}"
    return ""


def validate_sender_email(value: str) -> tuple[bool, str | None]:
    reason = sender_email_error_reason(value)
    return reason is None, reason


def reminder_send_estimate(result: Any) -> dict[str, int]:
    due_count = int(result.counts.get("due_reminders", 0))
    escalation_count = len(result.escalation_candidates)
    reminder_email_count = 1 if due_count > 0 and bool(result.recipients.get("reminder", [])) else 0
    escalation_email_count = 1 if escalation_count > 0 and bool(result.recipients.get("escalation", [])) else 0
    in_app_count = due_count
    return {
        "email_messages": reminder_email_count + escalation_email_count,
        "in_app_messages": in_app_count,
        "total_messages": reminder_email_count + escalation_email_count + in_app_count,
    }


def unknown_placeholder_actionable_message(unknown: dict[str, set[str]]) -> str:
    lines = [
        "Fix unknown placeholders before continuing.",
        "Use the placeholder picker or replace unknown tokens with supported fields.",
    ]
    for key, values in sorted(unknown.items()):
        lines.append(f"- {key}: {', '.join(sorted(values))}")
    return "\n".join(lines)


def evaluate_onboarding_reminder_health(
    last_reminder_run_at: Any,
    scheduler_or_settings_payload: dict[str, Any] | None,
    now: datetime | str | None = None,
) -> ReminderHealthStatus:
    expected_interval_hours = _expected_interval_hours(scheduler_or_settings_payload)
    current_time = _coerce_now(now)
    last_run = _coerce_datetime(last_reminder_run_at)

    if not last_run:
        return ReminderHealthStatus(
            severity="warning",
            message="Reminder scheduler has never run (or timestamp is invalid).",
            hours_or_days_late=None,
        )

    elapsed_hours = max(0, int((current_time - last_run).total_seconds() // 3600))
    if elapsed_hours > expected_interval_hours:
        late_hours = elapsed_hours - expected_interval_hours
        amount, unit = _format_lateness(late_hours)
        return ReminderHealthStatus(
            severity="overdue",
            message=f"Reminder scheduler is overdue by {amount} {unit}.",
            hours_or_days_late=amount,
        )

    return ReminderHealthStatus(
        severity="healthy",
        message="Reminder scheduler is healthy and running on schedule.",
        hours_or_days_late=0,
    )


def _expected_interval_hours(payload: dict[str, Any] | None) -> int:
    scheduler_settings = _extract_scheduler_settings(payload)
    if "expected_interval_hours" in scheduler_settings:
        return _coerce_positive_int(scheduler_settings.get("expected_interval_hours"), DEFAULT_EXPECTED_INTERVAL_HOURS)

    if "run_interval_minutes" in scheduler_settings:
        minutes = _coerce_positive_int(scheduler_settings.get("run_interval_minutes"), DEFAULT_EXPECTED_INTERVAL_HOURS * 60)
        return max(1, minutes // 60)

    return DEFAULT_EXPECTED_INTERVAL_HOURS


def _extract_scheduler_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    nested = payload.get("scheduler_settings")
    if isinstance(nested, dict):
        return nested
    return payload


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_now(now: datetime | str | None) -> datetime:
    parsed = _coerce_datetime(now)
    if parsed:
        return parsed
    return _normalize_datetime(datetime.now(timezone.utc))


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_datetime(value)

    parsed = parse_iso8601_datetime(value)
    if not parsed:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _format_lateness(late_hours: int) -> tuple[int, str]:
    if late_hours >= 48:
        return late_hours // 24, "days"
    return late_hours, "hours"


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


def parse_recipients(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_name_parts(name: str) -> tuple[str, str]:
    parts = str(name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _build_template_values(
    reminders: list[ReminderItem],
    school: str = "",
    runtime_values: dict[str, str] | None = None,
) -> dict[str, str]:
    lines = []
    due_dates = []
    names = []
    for item in reminders:
        due_label = item.due_date or "(no due date)"
        lines.append(f"- {item.employee_name}: {item.title} (due {due_label})")
        due_dates.append(due_label)
        names.append(item.employee_name)

    unique_names = sorted(set(names))
    first_name = ""
    last_name = ""
    if unique_names:
        first_name, last_name = _split_name_parts(unique_names[0])

    values = {
        "count": str(len(reminders)),
        "employee_summary": ", ".join(unique_names),
        "task_summary": "\n".join(lines),
        "due_date_summary": ", ".join(due_dates),
        "school": school,
        "first_name": first_name,
        "last_name": last_name,
    }
    if runtime_values:
        values.update({key: str(value) for key, value in runtime_values.items()})
    return values


def _send_email_message(settings: EmailSettings, recipients: list[str], subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = sanitize_email_subject(subject)
    message["From"] = settings.sender_email
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.use_tls:
            context = ssl.create_default_context()
            server.starttls(context=context)
        if settings.smtp_username:
            server.login(settings.smtp_username, resolve_smtp_password(settings.smtp_password))
        server.send_message(message)


def _validate_missing_values(context: str, templates: list[str], values: dict[str, str]) -> None:
    missing = sorted({
        key
        for template in templates
        for key in missing_placeholder_keys(template, values, context)
    })
    if not missing:
        return
    names = ", ".join(missing)
    raise ValueError(f"Missing values for placeholders: {names}")


def reminder_run_telemetry_counts(outcomes: list[object], dry_run: bool) -> dict[str, int]:
    recipient_count = 0
    sent_count = 0
    failed_count = 0
    blocked_count = 0
    skipped_count = 0
    warning_count = 0
    for outcome in outcomes:
        recipients = list(getattr(outcome, "recipients", []))
        recipient_count += len(recipients)
        attempted = bool(getattr(outcome, "attempted", False))
        success = bool(getattr(outcome, "success", False))
        if dry_run and not attempted:
            skipped_count += 1
        if attempted and success:
            sent_count += 1
        if attempted and not success:
            failed_count += 1
        if (not attempted) and (not success):
            blocked_count += 1
        if str(getattr(outcome, "error", "")).strip():
            warning_count += 1
    return {
        "recipient_count": recipient_count,
        "skipped_count": skipped_count,
        "warning_count": warning_count,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "blocked_count": blocked_count,
    }


def send_reminder_email(
    settings: EmailSettings,
    reminders: list[ReminderItem],
    monthly_lines: list[str] | None = None,
    school: str = "",
    runtime_values: dict[str, str] | None = None,
) -> str:
    recipients = parse_recipients(settings.reminder_recipients)
    if not recipients:
        return "No reminder recipients configured."
    if not settings.smtp_host or not settings.sender_email:
        return "SMTP settings are incomplete."

    template_values = _build_template_values(reminders, school=school, runtime_values=runtime_values)
    lines = ["Onboarding reminders:", "", template_values["task_summary"]]
    if monthly_lines:
        lines.extend(["", "Monthly website bio/photo check:", *monthly_lines])
    fallback_body = "\n".join(lines)

    _validate_missing_values(
        "onboarding_reminder",
        [settings.reminder_subject_template, settings.reminder_body_template],
        template_values,
    )

    subject = (
        render_template(
            settings.reminder_subject_template,
            template_values,
            context="onboarding_reminder",
            unknown_policy="empty",
        )
        or "Onboarding task reminders"
    )
    body = (
        render_template(
            settings.reminder_body_template,
            template_values,
            context="onboarding_reminder",
            unknown_policy="empty",
        )
        or fallback_body
    )
    _send_email_message(settings, recipients, subject, body)
    return f"Reminder email sent to {len(recipients)} recipient(s)."


def send_escalation_email(
    settings: EmailSettings,
    body_lines: list[str],
    reminders: list[ReminderItem] | None = None,
    school: str = "",
    runtime_values: dict[str, str] | None = None,
) -> str:
    recipients = parse_recipients(settings.director_and_owners)
    if not recipients:
        return "Escalation recipients are not configured."
    if not settings.smtp_host or not settings.sender_email:
        return "SMTP settings are incomplete."

    runtime_items = reminders or []
    template_values = _build_template_values(runtime_items, school=school, runtime_values=runtime_values)
    template_values["task_summary"] = "\n".join(body_lines)

    _validate_missing_values(
        "escalation",
        [settings.escalation_subject_template, settings.escalation_body_template],
        template_values,
    )

    subject = render_template(
        settings.escalation_subject_template,
        template_values,
        context="escalation",
        unknown_policy="empty",
    )
    if not subject:
        subject = "Escalation: Incomplete permit or LiveScan tasks"

    body = (
        render_template(
            settings.escalation_body_template,
            template_values,
            context="escalation",
            unknown_policy="empty",
        )
        or "\n".join(body_lines)
    )

    _send_email_message(settings, recipients, subject, body)
    return f"Escalation email sent to {len(recipients)} recipient(s)."


class OnboardingReminderRunner:
    def __init__(
        self,
        employees: list[Employee],
        templates: Any,
        email_settings: EmailSettings,
        reminder_sender: Any = send_reminder_email,
        escalation_sender: Any = send_escalation_email,
        runtime_values: dict[str, str] | None = None,
        metrics_logger: UxMetricsLogger | None = None,
    ) -> None:
        self.employees = employees
        self.templates = templates
        self.email_settings = email_settings
        self.reminder_sender = reminder_sender
        self.escalation_sender = escalation_sender
        self.runtime_values = dict(runtime_values or {})
        self.metrics_logger = metrics_logger

    def preview(self, now_date: date | None = None, include_escalation: bool = True) -> ReminderRunResult:
        return self.run(now_date=now_date, dry_run=True, include_escalation=include_escalation)

    def run(
        self,
        now_date: date | None = None,
        dry_run: bool = False,
        include_escalation: bool = True,
    ) -> ReminderRunResult:
        context = self._build_run_context(now_date=now_date, include_escalation=include_escalation)
        if not context.reminders:
            result = self._build_result(context=context, dry_run=dry_run, outcomes=[])
            self._emit_canonical_completion(dry_run=dry_run, outcomes=[])
            return result

        outcomes = self._send_phase(context=context, dry_run=dry_run)
        self._apply_state_updates(context=context, dry_run=dry_run, outcomes=outcomes)
        result = self._build_result(context=context, dry_run=dry_run, outcomes=outcomes)
        self._emit_canonical_completion(dry_run=dry_run, outcomes=outcomes)
        return result

    def _emit_canonical_completion(self, dry_run: bool, outcomes: list[SendOutcome]) -> None:
        if not self.metrics_logger:
            return
        counts = reminder_run_telemetry_counts(outcomes, dry_run)
        self.metrics_logger.log_onboarding_canonical_event(
            "ux.onboarding.reminder_run.completion",
            mode="dry_run" if dry_run else "live",
            recipient_count=counts["recipient_count"],
            skipped_count=counts["skipped_count"],
            warning_count=counts["warning_count"],
            sent_count=counts["sent_count"],
            failed_count=counts["failed_count"],
            blocked_count=counts["blocked_count"],
        )

    def _build_run_context(self, now_date: date | None, include_escalation: bool) -> ReminderRunContext:
        run_date = now_date or date.today()
        reminders = collect_due_reminders(self.employees, self.templates, run_date)
        monthly_lines = self._monthly_outstanding_lines(run_date)
        escalation_lines = self._collect_escalation_lines(reminders) if include_escalation else []
        recipients = {
            "reminder": parse_recipients(self.email_settings.reminder_recipients),
            "escalation": parse_recipients(self.email_settings.director_and_owners),
        }
        return ReminderRunContext(
            run_id=f"reminder_run_{uuid.uuid4().hex[:12]}",
            now_date=run_date,
            reminders=reminders,
            monthly_lines=monthly_lines,
            escalation_lines=escalation_lines,
            recipients=recipients,
            school=self._school_for_reminders(reminders),
            runtime_values=dict(self.runtime_values),
        )

    def _send_phase(self, context: ReminderRunContext, dry_run: bool) -> list[SendOutcome]:
        if dry_run:
            return self._dry_run_outcomes(context)

        outcomes = [
            self._send_reminders(context),
        ]
        if context.escalation_lines:
            outcomes.append(self._send_escalation(context))
        return outcomes

    def _dry_run_outcomes(self, context: ReminderRunContext) -> list[SendOutcome]:
        outcomes = [
            SendOutcome(
                phase="reminder",
                attempted=False,
                success=True,
                recipients=context.recipients["reminder"],
                item_count=len(context.reminders),
                message="Dry-run: reminder send skipped.",
            )
        ]
        if context.escalation_lines:
            outcomes.append(
                SendOutcome(
                    phase="escalation",
                    attempted=False,
                    success=True,
                    recipients=context.recipients["escalation"],
                    item_count=len(context.escalation_lines),
                    message="Dry-run: escalation send skipped.",
                )
            )
        return outcomes

    def _send_reminders(self, context: ReminderRunContext) -> SendOutcome:
        if not context.recipients["reminder"]:
            return SendOutcome(
                phase="reminder",
                attempted=False,
                success=False,
                recipients=[],
                item_count=len(context.reminders),
                error="No reminder recipients configured.",
            )

        try:
            message = self.reminder_sender(
                self.email_settings,
                context.reminders,
                context.monthly_lines,
                context.school,
                context.runtime_values,
            )
            return SendOutcome(
                phase="reminder",
                attempted=True,
                success=True,
                recipients=context.recipients["reminder"],
                item_count=len(context.reminders),
                message=message,
            )
        except Exception as exc:
            return SendOutcome(
                phase="reminder",
                attempted=True,
                success=False,
                recipients=context.recipients["reminder"],
                item_count=len(context.reminders),
                error=str(exc),
            )

    def _send_escalation(self, context: ReminderRunContext) -> SendOutcome:
        if not context.recipients["escalation"]:
            return SendOutcome(
                phase="escalation",
                attempted=False,
                success=False,
                recipients=[],
                item_count=len(context.escalation_lines),
                error="Escalation recipients are not configured.",
            )

        try:
            message = self.escalation_sender(
                self.email_settings,
                context.escalation_lines,
                context.reminders,
                context.school,
                context.runtime_values,
            )
            return SendOutcome(
                phase="escalation",
                attempted=True,
                success=True,
                recipients=context.recipients["escalation"],
                item_count=len(context.escalation_lines),
                message=message,
            )
        except Exception as exc:
            return SendOutcome(
                phase="escalation",
                attempted=True,
                success=False,
                recipients=context.recipients["escalation"],
                item_count=len(context.escalation_lines),
                error=str(exc),
            )

    def _apply_state_updates(self, context: ReminderRunContext, dry_run: bool, outcomes: list[SendOutcome]) -> None:
        if dry_run:
            return
        reminder_outcome = self._outcome_for_phase(outcomes, "reminder")
        if not reminder_outcome or not reminder_outcome.success:
            return

        lookup = {employee.id: employee for employee in self.employees}
        for reminder in context.reminders:
            employee = lookup.get(reminder.employee_id)
            if not employee:
                continue
            self._mark_task_sent(employee, reminder.task_id, context.now_date)

    def _outcome_for_phase(self, outcomes: list[SendOutcome], phase: str) -> SendOutcome | None:
        for outcome in outcomes:
            if outcome.phase == phase:
                return outcome
        return None

    def _mark_task_sent(self, employee: Employee, task_id: str, run_date: date) -> None:
        for task in employee.tasks:
            if task.id != task_id:
                continue
            mark_reminder_sent(task, run_date)
            return

    def _school_for_reminders(self, reminders: list[ReminderItem]) -> str:
        employees = self._employees_for_reminders(reminders)
        for employee in employees:
            school = str(getattr(employee, "school", "")).strip()
            if school:
                return school
        return ""

    def _employees_for_reminders(self, reminders: list[ReminderItem]) -> list[Employee]:
        lookup = {employee.id: employee for employee in self.employees}
        employees: list[Employee] = []
        for reminder in reminders:
            employee = lookup.get(reminder.employee_id)
            if not employee:
                continue
            employees.append(employee)
        return employees

    def _collect_escalation_lines(self, reminders: list[ReminderItem]) -> list[str]:
        escalation_items = [item for item in reminders if "Escalation:" in item.title]
        if not escalation_items:
            return []

        lines = ["The following employees still have incomplete permit or LiveScan tasks:"]
        lines.extend([f"- {item.employee_name} ({item.title})" for item in escalation_items])
        return lines

    def _monthly_outstanding_lines(self, run_date: date) -> list[str]:
        if run_date.day != 1:
            return []

        rows: list[str] = []
        for employee in self.employees:
            pending = [task.title for task in employee.tasks if not task.completed]
            if not pending:
                continue
            rows.append(f"- {employee.name}: {', '.join(pending)}")
        return rows

    def _build_result(self, context: ReminderRunContext, dry_run: bool, outcomes: list[SendOutcome]) -> ReminderRunResult:
        tasks = [
            {
                "employee_id": item.employee_id,
                "employee_name": item.employee_name,
                "task_id": item.task_id,
                "title": item.title,
                "due_date": item.due_date,
            }
            for item in context.reminders
        ]
        counts = self._build_counts(context, outcomes)
        task_breakdown = self._build_task_breakdown(tasks)
        escalation_candidates = self._escalation_candidates(context.reminders)
        channel_results = self._build_channel_results(counts, outcomes)
        error_summaries = [outcome.error for outcome in outcomes if outcome.error]
        return ReminderRunResult(
            run_id=context.run_id,
            ran_at=datetime.combine(context.now_date, datetime.min.time()).isoformat(),
            dry_run=dry_run,
            recipients=context.recipients,
            tasks=tasks,
            counts=counts,
            outcomes=outcomes,
            task_breakdown=task_breakdown,
            escalation_candidates=escalation_candidates,
            channel_results=channel_results,
            error_summaries=error_summaries,
        )

    @staticmethod
    def _build_task_breakdown(tasks: list[dict[str, str | None]]) -> dict[str, list[dict[str, str | None]]]:
        grouped: dict[str, list[dict[str, str | None]]] = {}
        for task in tasks:
            employee_name = str(task.get("employee_name") or "Unknown")
            grouped.setdefault(employee_name, []).append(task)
        return grouped

    @staticmethod
    def _escalation_candidates(reminders: list[ReminderItem]) -> list[dict[str, str | None]]:
        return [
            {
                "employee_id": item.employee_id,
                "employee_name": item.employee_name,
                "task_id": item.task_id,
                "title": item.title,
                "due_date": item.due_date,
            }
            for item in reminders
            if "Escalation:" in item.title
        ]

    @staticmethod
    def _build_channel_results(counts: dict[str, int], outcomes: list[SendOutcome]) -> dict[str, dict[str, int]]:
        email_attempted = sum(1 for outcome in outcomes if outcome.attempted)
        email_sent = sum(1 for outcome in outcomes if outcome.success)
        email_failed = sum(1 for outcome in outcomes if outcome.attempted and not outcome.success)
        return {
            "email": {
                "attempted": email_attempted,
                "sent": email_sent,
                "failed": email_failed,
            },
            "in_app": {
                "attempted": 0,
                "sent": counts["due_reminders"],
                "failed": 0,
            },
        }

    def _build_counts(self, context: ReminderRunContext, outcomes: list[SendOutcome]) -> dict[str, int]:
        attempted = sum(1 for item in outcomes if item.attempted)
        successful = sum(1 for item in outcomes if item.success)
        failed = sum(1 for item in outcomes if item.attempted and not item.success)
        return {
            "due_reminders": len(context.reminders),
            "escalation_lines": len(context.escalation_lines),
            "monthly_lines": len(context.monthly_lines),
            "send_attempts": attempted,
            "successful_sends": successful,
            "failed_sends": failed,
        }


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

_COMPAT_MODULES: tuple[str, ...] = ()

_WRAPPER_POLICY = (
    "Legacy onboarding modules are compatibility wrappers during flattening. "
    "New production imports should prefer onboarding_operations."
)


def available_modules() -> tuple[str, ...]:
    return _COMPAT_MODULES


def module_ownership() -> dict[str, str]:
    return {module_name: "onboarding_operations" for module_name in _COMPAT_MODULES}


def wrapper_policy() -> str:
    return _WRAPPER_POLICY


def load_compat_module(module_name: str) -> ModuleType:
    if module_name not in _COMPAT_MODULES:
        raise AttributeError(f"{module_name!r} is not part of onboarding_operations")
    return import_module(module_name)


def public_symbols(module_name: str | None = None) -> tuple[str, ...]:
    module_names = (module_name,) if module_name is not None else _COMPAT_MODULES
    symbols: set[str] = set()
    for compat_name in module_names:
        module = load_compat_module(compat_name)
        symbols.update(name for name in dir(module) if not name.startswith("_"))
    return tuple(sorted(symbols))


def resolve_compat_symbol(symbol_name: str) -> Any:
    for module_name in _COMPAT_MODULES:
        module = import_module(module_name)
        if hasattr(module, symbol_name):
            return getattr(module, symbol_name)
    raise AttributeError(f"onboarding_operations has no attribute {symbol_name!r}")


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(f"onboarding_operations has no attribute {name!r}")
    if name in _COMPAT_MODULES:
        return import_module(name)
    return resolve_compat_symbol(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_COMPAT_MODULES) | set(public_symbols()))
