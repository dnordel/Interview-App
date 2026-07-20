from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from onboarding_store import OnboardingStore


@dataclass(frozen=True)
class ReminderPreviewMessage:
    school: str
    role: str
    recipient: str
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReminderPreview:
    token: str
    generated_at: datetime
    expires_at: datetime
    data_revision: int
    config_revision: str
    messages: tuple[ReminderPreviewMessage, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ReminderSendResult:
    run_id: str
    sent_count: int
    failed_count: int
    skipped_count: int


class OnboardingReminderCoordinator:
    def __init__(self, store: OnboardingStore, *, role: str) -> None:
        self.store = store
        self.role = str(role or "").strip().casefold()
        self._previews: dict[str, ReminderPreview] = {}
        self._failed_runs: dict[str, tuple[ReminderPreviewMessage, ...]] = {}

    def preview(
        self,
        *,
        recipient_resolver: Callable[[str, str], str],
        admin_fallback_email: str,
        now: datetime,
        school_scope: str = "",
        config_revision: str = "",
    ) -> ReminderPreview:
        current = _aware_datetime(now)
        fallback = _email(admin_fallback_email, "Admin fallback email")
        tasks = self.store.list_tasks(school=school_scope)
        statuses = {task.id: task.status for task in tasks}
        grouped: dict[tuple[str, str], list[str]] = {}
        warnings: list[str] = []
        for task in tasks:
            if task.status in {"completed", "cancelled", "blocked"} or task.due_date > current.date().isoformat():
                continue
            if any(statuses.get(dependency_id) != "completed" for dependency_id in task.dependency_ids):
                continue
            roles = (task.owner_role, *task.watcher_roles)
            for role in dict.fromkeys(value for value in roles if value):
                grouped.setdefault((task.school, role), []).append(task.id)
        messages: list[ReminderPreviewMessage] = []
        for (school, role), task_ids in sorted(grouped.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold())):
            recipient = str(recipient_resolver(school, role) or "").strip()
            if recipient:
                recipient = _email(recipient, f"{role} recipient")
            else:
                recipient = fallback
                warnings.append(f"{school} {role} has no email; Admin fallback will be used.")
            messages.append(
                ReminderPreviewMessage(
                    school=school,
                    role=role,
                    recipient=recipient,
                    task_ids=tuple(sorted(task_ids)),
                )
            )
        preview = ReminderPreview(
            token=uuid4().hex,
            generated_at=current,
            expires_at=current + timedelta(minutes=10),
            data_revision=self.store.data_revision(),
            config_revision=str(config_revision or ""),
            messages=tuple(messages),
            warnings=tuple(warnings),
        )
        self._previews[preview.token] = preview
        return preview

    def send(
        self,
        token: str,
        *,
        sender: Callable[[ReminderPreviewMessage], None],
        now: datetime,
        confirmed: bool,
        admin_override_reason: str = "",
        config_revision: str = "",
    ) -> ReminderSendResult:
        if not confirmed:
            raise ValueError("Reminder send requires explicit final confirmation.")
        preview = self._previews.get(str(token or "").strip())
        if preview is None:
            raise ValueError("Reminder preview token is invalid.")
        current = _aware_datetime(now)
        if current > preview.expires_at:
            raise ValueError("Reminder preview expired; create a fresh preview.")
        if self.store.data_revision() != preview.data_revision:
            raise ValueError("Onboarding data changed; create a fresh preview.")
        if str(config_revision or "") != preview.config_revision:
            raise ValueError("Notification configuration changed; create a fresh preview.")
        local_day = current.date().isoformat()
        duplicate_schools = {
            message.school
            for message in preview.messages
            if self.store.has_sent_reminder_batch(school=message.school, local_day=local_day)
        }
        if duplicate_schools:
            if self.role != "admin":
                raise ValueError("Reminder batch was already sent for this school and day.")
            if not str(admin_override_reason or "").strip():
                raise ValueError("Admin duplicate-send override requires a reason.")
        run_id = uuid4().hex
        sent = 0
        failed = 0
        failed_messages: list[ReminderPreviewMessage] = []
        for message in preview.messages:
            try:
                sender(message)
                state = "sent"
                error_category = ""
                sent += 1
            except Exception as exc:
                state = "failed"
                error_category = type(exc).__name__
                failed += 1
                failed_messages.append(message)
            self.store.record_reminder_message_run(
                run_id=run_id,
                school=message.school,
                local_day=local_day,
                role=message.role,
                state=state,
                task_count=len(message.task_ids),
                error_category=error_category,
                created_at=current.isoformat(),
            )
        self._previews.pop(preview.token, None)
        if failed_messages:
            self._failed_runs[run_id] = tuple(failed_messages)
        return ReminderSendResult(run_id=run_id, sent_count=sent, failed_count=failed, skipped_count=0)

    def retry_failed(
        self,
        run_id: str,
        *,
        sender: Callable[[ReminderPreviewMessage], None],
        now: datetime,
        confirmed: bool,
    ) -> ReminderSendResult:
        if not confirmed:
            raise ValueError("Reminder retry requires explicit final confirmation.")
        source_run_id = str(run_id or "").strip()
        messages = self._failed_runs.pop(source_run_id, ())
        if not messages:
            raise ValueError("Reminder run has no failed messages to retry.")
        current = _aware_datetime(now)
        retry_run_id = uuid4().hex
        sent = 0
        failed_messages: list[ReminderPreviewMessage] = []
        for message in messages:
            try:
                sender(message)
                state = "sent"
                error_category = ""
                sent += 1
            except Exception as exc:
                state = "failed"
                error_category = type(exc).__name__
                failed_messages.append(message)
            self.store.record_reminder_message_run(
                run_id=retry_run_id,
                school=message.school,
                local_day=current.date().isoformat(),
                role=message.role,
                state=state,
                task_count=len(message.task_ids),
                error_category=error_category,
                created_at=current.isoformat(),
            )
        if failed_messages:
            self._failed_runs[retry_run_id] = tuple(failed_messages)
        return ReminderSendResult(
            run_id=retry_run_id,
            sent_count=sent,
            failed_count=len(failed_messages),
            skipped_count=0,
        )


def _aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Reminder time must include a timezone.")
    return value


def _email(value: str, label: str) -> str:
    text = str(value or "").strip()
    if "@" not in text or text.startswith("@") or text.endswith("@") or " " in text:
        raise ValueError(f"{label} must be a valid email address.")
    return text
