from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from email_security import is_valid_email_address
from notification_models import NotificationRule, NotificationSendResult
from notification_store import NotificationStore
from onboarding_operations import EmailSettings, _send_email_message
from platform_services import USER_ARTIFACTS_DIR, atomic_write_json, safe_read_json


NOTIFICATION_RULES_PATH = USER_ARTIFACTS_DIR / "notification_rules.sqlite3"
EMAIL_ACCOUNT_SETTINGS_PATH = USER_ARTIFACTS_DIR / "email_account_settings.json"
SUPPORTED_NOTIFICATION_EVENTS = (
    "staffing.assignment.need_now",
    "staffing.assignment.coming",
    "staffing.assignment.filled",
    "staffing.assignment.replace",
    "staffing.assignment.not_needed",
    "staffing.permit.updated",
    "offer.generated",
    "offer.approved",
    "offer.accepted",
    "offer.welcome_email_sent",
    "interview.rating.hire",
    "interview.rating.borderline",
    "onboarding.task.created",
    "onboarding.task.completed",
    "onboarding.task.overdue",
)
NOTIFICATION_TEMPLATE_FIELDS = (
    "candidate_name",
    "person_name",
    "school",
    "director_name",
    "position",
    "position_name",
    "position_type",
    "outcome",
    "score",
    "offer_status",
    "interview_date",
    "history_id",
    "generated_date",
    "start_date",
    "date_notice_given",
    "shift_start",
    "shift_end",
    "notice_given",
    "final_working_day",
    "last_working_day",
    "has_degree",
    "degree_type",
    "degree_in_ece",
    "ece_units_completed",
    "total_units_completed",
    "infant_toddler_class_completed",
    "years_experience",
    "permit_status",
    "assignment_status",
    "classroom",
    "slot_group",
)


class NotificationService:
    def __init__(
        self,
        *,
        store: NotificationStore | None = None,
        email_settings: EmailSettings | None = None,
        send_email: Callable[[EmailSettings, list[str], str, str], Any] | None = None,
        current_date: Callable[[], date] | None = None,
    ) -> None:
        self.store = store or NotificationStore(NOTIFICATION_RULES_PATH)
        self.email_settings = email_settings or EmailSettings()
        self.send_email = send_email or _send_email_message
        self.current_date = current_date or date.today

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, str],
        idempotency_key: str,
    ) -> list[NotificationSendResult]:
        event_type = str(event_type or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not event_type:
            raise ValueError("Notification event type is required.")
        if not idempotency_key:
            raise ValueError("Notification idempotency key is required.")

        rules = [rule for rule in self.store.list_rules(event_type) if rule.active]
        results: list[NotificationSendResult] = []
        for rule in rules:
            results.append(self._send_for_rule(rule, event_type, payload, idempotency_key))
        return results

    def _send_for_rule(
        self,
        rule: NotificationRule,
        event_type: str,
        payload: dict[str, str],
        idempotency_key: str,
    ) -> NotificationSendResult:
        rule_id = int(rule.id) if rule.id is not None else None
        if rule_id is not None and self.store.has_send_attempt(rule_id, idempotency_key):
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="duplicate")

        trigger_status = self._trigger_block_status(rule, payload)
        if trigger_status:
            if trigger_status == "not_due" and rule_id is not None:
                due_date = self._date_offset_due_date(rule, payload)
                if due_date is not None:
                    self.store.schedule_notification(
                        event_type=event_type,
                        rule_id=rule_id,
                        idempotency_key=idempotency_key,
                        due_date=due_date,
                        payload=payload,
                    )
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status=trigger_status)

        recipients = [recipient.email.strip() for recipient in rule.recipients if recipient.active]
        blocked_error = self._blocked_reason(rule, recipients, payload)
        if blocked_error:
            self.store.record_send_attempt(
                event_type=event_type,
                rule_id=rule_id,
                idempotency_key=idempotency_key,
                recipient_count=len(recipients),
                status="blocked",
                error=blocked_error,
            )
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="blocked", recipient_count=len(recipients), error=blocked_error)

        values = {str(key): str(value) for key, value in payload.items()}
        subject = _render_notification_template(rule.subject_template, values)
        body = _render_notification_template(rule.body_template, values)
        try:
            self.send_email(self.email_settings, recipients, subject, body)
        except Exception as exc:
            error = _sanitize_error(str(exc))
            self.store.record_send_attempt(
                event_type=event_type,
                rule_id=rule_id,
                idempotency_key=idempotency_key,
                recipient_count=len(recipients),
                status="failed",
                error=error,
            )
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="failed", recipient_count=len(recipients), error=error)

        self.store.record_send_attempt(
            event_type=event_type,
            rule_id=rule_id,
            idempotency_key=idempotency_key,
            recipient_count=len(recipients),
            status="sent",
        )
        return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="sent", recipient_count=len(recipients))

    def run_due_notifications(self) -> list[NotificationSendResult]:
        results: list[NotificationSendResult] = []
        for scheduled in self.store.list_due_scheduled_notifications(self.current_date()):
            try:
                rule = self.store.get_rule(int(scheduled["rule_id"]))
                result = self._send_for_rule(
                    rule,
                    str(scheduled["event_type"]),
                    dict(scheduled["payload"]),
                    str(scheduled["idempotency_key"]),
                )
            except Exception as exc:
                result = NotificationSendResult(
                    event_type=str(scheduled.get("event_type", "")),
                    rule_id=int(scheduled.get("rule_id", 0) or 0),
                    status="failed",
                    error=_sanitize_error(str(exc)),
                )
            if result.status in {"sent", "blocked", "failed", "duplicate"}:
                self.store.mark_scheduled_notification(int(scheduled["id"]), result.status)
            results.append(result)
        return results

    def _trigger_block_status(self, rule: NotificationRule, payload: dict[str, str]) -> str:
        timing = str(rule.trigger_timing or "event").strip()
        if timing == "event":
            return ""
        if timing != "date_offset":
            return "blocked"
        due_date = self._date_offset_due_date(rule, payload)
        if due_date is None:
            return "blocked"
        if due_date > self.current_date():
            return "not_due"
        return ""

    def _date_offset_due_date(self, rule: NotificationRule, payload: dict[str, str]) -> date | None:
        field = str(rule.date_field or "").strip()
        if not field:
            return None
        try:
            basis_date = date.fromisoformat(str(payload.get(field, "")).strip())
        except ValueError:
            return None
        return basis_date + timedelta(days=int(rule.offset_days))

    def _blocked_reason(self, rule: NotificationRule, recipients: list[str], payload: dict[str, str]) -> str:
        if not recipients:
            return "No active recipients configured."
        if not self.email_settings.smtp_host or not self.email_settings.sender_email:
            return "SMTP settings are incomplete."
        invalid = [email for email in recipients if not is_valid_email_address(email)]
        if invalid:
            return "One or more recipient addresses are invalid."
        missing = sorted(_missing_template_keys([rule.subject_template, rule.body_template], payload))
        if missing:
            return f"Missing values for placeholders: {', '.join(missing)}"
        return ""


def emit_notification_event(
    event_type: str,
    payload: dict[str, str],
    idempotency_key: str,
    *,
    store_path: Path = NOTIFICATION_RULES_PATH,
    email_settings: EmailSettings | None = None,
) -> list[NotificationSendResult]:
    service = NotificationService(store=NotificationStore(store_path), email_settings=email_settings)
    return service.emit_event(event_type, payload, idempotency_key)


def notification_service_from_onboarding(
    *,
    root_dir: Path,
    store_path: Path = NOTIFICATION_RULES_PATH,
) -> NotificationService:
    from onboarding_operations import JsonStore

    state = JsonStore(root_dir).load()
    return NotificationService(store=NotificationStore(store_path), email_settings=state.email_settings)


def load_email_account_settings(path: Path = EMAIL_ACCOUNT_SETTINGS_PATH) -> EmailSettings:
    payload = safe_read_json(Path(path), default={}, expected_type=dict)
    source = payload.get("email", payload) if isinstance(payload, dict) else {}
    return EmailSettings.from_dict(source if isinstance(source, dict) else {})


def save_email_account_settings(settings: EmailSettings, path: Path = EMAIL_ACCOUNT_SETTINGS_PATH) -> None:
    atomic_write_json(Path(path), {"email": settings.to_dict()}, indent=2, ensure_ascii=False)


def notification_service_from_email_account_settings(
    *,
    settings_path: Path = EMAIL_ACCOUNT_SETTINGS_PATH,
    store_path: Path = NOTIFICATION_RULES_PATH,
) -> NotificationService:
    return NotificationService(
        store=NotificationStore(store_path),
        email_settings=load_email_account_settings(settings_path),
    )


def _missing_template_keys(templates: list[str], payload: dict[str, str]) -> set[str]:
    available = {str(key) for key in payload}
    found: set[str] = set()
    for template in templates:
        found.update(match.group(1) for match in re.finditer(r"{([A-Za-z_][A-Za-z0-9_]*)}", str(template or "")))
    return found - available


def _render_notification_template(template: str, payload: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return payload.get(match.group(1), "")

    return re.sub(r"{([A-Za-z_][A-Za-z0-9_]*)}", replace, str(template or ""))


def _sanitize_error(value: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", str(value or ""))
    text = re.sub(r"(?i)(password|token|secret)=\S+", r"\1=[REDACTED]", text)
    return text[:300]
