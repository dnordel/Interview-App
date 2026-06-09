from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import os
from typing import Any
import uuid

DATE_FMT = "%Y-%m-%d"
SMTP_PASSWORD_ENV_KEYS = ("ONBOARDING_SMTP_PASSWORD", "SMTP_PASSWORD")


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
        source = payload if isinstance(payload, dict) else {}
        return cls(
            name=str(source.get("name", "") or "").strip(),
            school=str(source.get("school", "") or "").strip(),
            acceptance_date=str(source.get("acceptance_date", "") or "").strip(),
            start_date=str(source.get("start_date", "") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "school": self.school,
            "acceptance_date": self.acceptance_date,
            "start_date": self.start_date,
        }

    def has_prefill(self) -> bool:
        return any(self.to_dict().values())


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
