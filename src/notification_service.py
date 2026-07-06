from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from email_security import is_valid_email_address
from notification_models import NotificationRecipient, NotificationRule, NotificationSendResult
from notification_store import NotificationStore
from onboarding_operations import EmailSettings, _send_email_message
from platform_services import USER_ARTIFACTS_DIR, atomic_write_json, safe_read_json


NOTIFICATION_RULES_PATH = USER_ARTIFACTS_DIR / "notification_rules.sqlite3"
EMAIL_ACCOUNT_SETTINGS_PATH = USER_ARTIFACTS_DIR / "email_account_settings.json"
SUPPORTED_NOTIFICATION_EVENTS = (
    "staffing.assignment.created",
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
HIRING_MANAGER_EMAIL = "recruiting@launchpadpreschool.com"
EXECUTIVE_DIRECTOR_EMAIL = "deidre@launchpadpreschool.com"
DIRECTOR_EMAILS_BY_SCHOOL = {
    "hawthorne": "director@launchpadpreschoolHAW.com",
    "north long beach": "director@launchpadpreschoolNLB.com",
    "palmdale": "director@launchpadpreschoolPMD.com",
}
NOTIFICATION_TEMPLATE_FIELDS = (
    "candidate_name",
    "candidate",
    "candidate_email",
    "person_name",
    "school",
    "school_code",
    "school_location",
    "director_name",
    "hiring_manager_name",
    "recruiter_name",
    "company_name",
    "department",
    "location",
    "program",
    "position",
    "position_name",
    "position_type",
    "outcome",
    "score",
    "offer_status",
    "offer_path",
    "offer_pdf_path",
    "onboarding_guide_path",
    "reply_by_date",
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
        send_email: Callable[..., Any] | None = None,
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

    def send_test(
        self,
        rule_id: int,
        payload: dict[str, str],
        idempotency_key: str,
    ) -> NotificationSendResult:
        rule = self.store.get_rule(int(rule_id))
        return self._send_for_rule(
            rule,
            f"{rule.event_type}.test",
            payload,
            str(idempotency_key or "").strip(),
            bypass_trigger=True,
        )

    def _send_for_rule(
        self,
        rule: NotificationRule,
        event_type: str,
        payload: dict[str, str],
        idempotency_key: str,
        *,
        bypass_trigger: bool = False,
    ) -> NotificationSendResult:
        rule_id = int(rule.id) if rule.id is not None else None
        if rule_id is not None and self.store.has_send_attempt(rule_id, idempotency_key):
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="duplicate")

        trigger_status = "" if bypass_trigger else self._trigger_block_status(rule, payload)
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

        recipients, _summary, recipient_error = resolve_notification_recipients(rule, payload)
        blocked_error = recipient_error or self._blocked_reason(rule, recipients, payload)
        if blocked_error:
            self.store.record_send_attempt(
                event_type=event_type,
                rule_id=rule_id,
                idempotency_key=idempotency_key,
                recipient_count=len(recipients),
                status="blocked",
                error=_sanitize_error(blocked_error),
            )
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="blocked", recipient_count=len(recipients), error=blocked_error)

        values = {str(key): str(value) for key, value in payload.items()}
        subject = _render_notification_template(rule.subject_template, values)
        body = _render_notification_template(rule.body_template, values)
        attachment_paths, attachment_error = _notification_attachment_paths(event_type, payload)
        if attachment_error:
            self.store.record_send_attempt(
                event_type=event_type,
                rule_id=rule_id,
                idempotency_key=idempotency_key,
                recipient_count=len(recipients),
                status="blocked",
                error=_sanitize_error(attachment_error),
            )
            return NotificationSendResult(event_type=event_type, rule_id=rule_id, status="blocked", recipient_count=len(recipients), error=attachment_error)
        try:
            if attachment_paths:
                self.send_email(self.email_settings, recipients, subject, body, attachment_paths)
            else:
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


def resolve_notification_recipients(rule: NotificationRule, payload: dict[str, str]) -> tuple[list[str], str, str]:
    recipients: list[str] = []
    summary_parts: list[str] = []
    for recipient in rule.recipients:
        if not recipient.active:
            continue
        resolved, summary, error = _resolve_notification_recipient(recipient, payload)
        if error:
            return [], "", error
        if resolved and resolved not in recipients:
            recipients.append(resolved)
            if summary and summary not in summary_parts:
                summary_parts.append(summary)
    return recipients, " + ".join(summary_parts), ""


def _resolve_notification_recipient(
    recipient: NotificationRecipient,
    payload: dict[str, str],
) -> tuple[str, str, str]:
    recipient_type = str(recipient.recipient_type or "email").strip() or "email"
    if recipient_type == "email":
        email = str(recipient.email or "").strip()
        label = str(recipient.role_label or recipient.name or email).strip()
        return email, label, ""
    if recipient_type != "role":
        return "", "", "Unknown notification recipient role."

    role_key = str(recipient.role_key or "").strip()
    if role_key == "hiring_manager":
        return HIRING_MANAGER_EMAIL, "hiring manager", ""
    if role_key == "executive_director":
        return EXECUTIVE_DIRECTOR_EMAIL, "executive director", ""
    if role_key == "candidate":
        candidate_email = str(payload.get("candidate_email", "") or payload.get("email", "")).strip()
        if not is_valid_email_address(candidate_email):
            return "", "", "Candidate recipient requires a valid candidate email."
        return candidate_email, "candidate", ""
    if role_key == "director":
        school_key = str(payload.get("school", "")).strip().casefold()
        director_email = DIRECTOR_EMAILS_BY_SCHOOL.get(school_key)
        if not director_email:
            return "", "", "Director recipient requires a supported school."
        return director_email, "school director", ""
    return "", "", "Unknown notification recipient role."


def _notification_attachment_paths(event_type: str, payload: dict[str, str]) -> tuple[list[str], str]:
    normalized_event = str(event_type or "").removesuffix(".test")
    paths: list[str] = []
    if normalized_event == "offer.approved":
        offer_pdf = str(payload.get("offer_pdf_path", "") or "").strip()
        if not offer_pdf:
            return [], "Offer PDF attachment is required."
        paths.append(offer_pdf)
    onboarding_guide = str(payload.get("onboarding_guide_path", "") or "").strip()
    if normalized_event == "offer.accepted" and onboarding_guide:
        paths.append(onboarding_guide)
    for path_text in paths:
        path = Path(path_text)
        if not path.is_file():
            return [], "Notification attachment file was not found."
    return paths, ""


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
