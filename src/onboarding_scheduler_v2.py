from __future__ import annotations

from datetime import datetime
from typing import Callable

from onboarding_reminders_v2 import ReminderPreviewMessage, ReminderSendResult
from onboarding_service import OnboardingService


class OnboardingAutomaticReminderScheduler:
    """Admin-only weekday 8 AM reminder sender using the normal preview gate."""

    def __init__(
        self,
        service: OnboardingService,
        *,
        recipient_resolver: Callable[[str, str], str],
        admin_fallback_email: str | Callable[[], str],
        sender: Callable[[ReminderPreviewMessage], None],
        config_revision: Callable[[], str] | None = None,
    ) -> None:
        if service.access.role != "admin":
            raise PermissionError("Automatic onboarding reminders are Admin-only.")
        self.service = service
        self.recipient_resolver = recipient_resolver
        self.admin_fallback_email = admin_fallback_email
        self.sender = sender
        self.config_revision = config_revision or (lambda: "")

    def run_if_due(self, now: datetime) -> ReminderSendResult | None:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Scheduler time must include a timezone.")
        if now.weekday() >= 5 or now.hour != 8:
            return None
        local_day = now.date().isoformat()
        if self.service.scheduler_run_recorded(local_day=local_day):
            return None
        preview = self.service.preview_reminders(
            recipient_resolver=self.recipient_resolver,
            admin_fallback_email=(
                self.admin_fallback_email()
                if callable(self.admin_fallback_email)
                else self.admin_fallback_email
            ),
            now=now,
            config_revision=self.config_revision(),
        )
        result = self.service.send_reminder_preview(
            preview.token,
            sender=self.sender,
            now=now,
            confirmed=True,
            config_revision=self.config_revision(),
        )
        state = "success"
        if result.failed_count:
            state = "failed" if result.sent_count == 0 else "partial"
        elif result.sent_count == 0:
            state = "no_due"
        self.service.record_scheduler_run(
            local_day=local_day,
            state=state,
            sent_count=result.sent_count,
            failed_count=result.failed_count,
            skipped_count=result.skipped_count,
            created_at=now.isoformat(),
        )
        return result
