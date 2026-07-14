from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
import smtplib
import ssl
from typing import Any

from email_security import is_valid_email_address, sanitize_email_subject
from notification_models import NotificationRecipient, NotificationRule, NotificationSendResult
from notification_store import NotificationStore
from notification_templates import (
    NOTIFICATION_TEMPLATE_FIELDS,
    render_notification_templates,
    validate_notification_rule,
)
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
@dataclass
class EmailSettings:
    account_label: str = ""
    display_name: str = ""
    authentication_type: str = "Normal password"
    account_type: str = "IMAP"
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    sender_email: str = ""
    use_ssl: bool = False
    use_starttls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    use_tls: bool = True
    imap_or_pop_host: str = ""
    imap_or_pop_port: int = 993
    incoming_encryption: str = "SSL/TLS"
    smtp_encryption: str = "STARTTLS"
    remember_password: bool = True
    require_spa: bool = False
    use_same_credentials: bool = True
    director_and_owners: str = ""
    reminder_recipients: str = ""
    reminder_subject_template: str = ""
    reminder_body_template: str = ""
    escalation_subject_template: str = ""
    escalation_body_template: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EmailSettings":
        source = payload if isinstance(payload, dict) else {}
        use_tls = bool(source.get("use_tls", True))
        smtp_encryption = source.get("smtp_encryption", "STARTTLS" if use_tls else "None")
        return cls(
            account_label=str(source.get("account_label", "") or ""),
            display_name=str(source.get("display_name", "") or ""),
            authentication_type=str(source.get("authentication_type", "Normal password") or "Normal password"),
            account_type=str(source.get("account_type", "IMAP") or "IMAP"),
            smtp_host=str(source.get("smtp_host", "") or ""),
            smtp_port=int(source.get("smtp_port", 587) or 587),
            username=str(source.get("username", source.get("smtp_username", "")) or ""),
            password=str(source.get("password", source.get("smtp_password", "")) or ""),
            sender_email=str(source.get("sender_email", "") or ""),
            use_ssl=bool(source.get("use_ssl", False)),
            use_starttls=bool(source.get("use_starttls", True)),
            smtp_username=str(source.get("smtp_username", source.get("username", "")) or ""),
            smtp_password=str(source.get("smtp_password", source.get("password", "")) or ""),
            use_tls=use_tls,
            imap_or_pop_host=str(source.get("imap_or_pop_host", "") or ""),
            imap_or_pop_port=int(source.get("imap_or_pop_port", 993) or 993),
            incoming_encryption=str(source.get("incoming_encryption", "SSL/TLS") or "SSL/TLS"),
            smtp_encryption=str(smtp_encryption or "STARTTLS"),
            remember_password=bool(source.get("remember_password", True)),
            require_spa=bool(source.get("require_spa", False)),
            use_same_credentials=bool(source.get("use_same_credentials", True)),
            director_and_owners=str(source.get("director_and_owners", "") or ""),
            reminder_recipients=str(source.get("reminder_recipients", "") or ""),
            reminder_subject_template=str(source.get("reminder_subject_template", "") or ""),
            reminder_body_template=str(source.get("reminder_body_template", "") or ""),
            escalation_subject_template=str(source.get("escalation_subject_template", "") or ""),
            escalation_body_template=str(source.get("escalation_body_template", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_label": self.account_label,
            "display_name": self.display_name,
            "authentication_type": self.authentication_type,
            "account_type": self.account_type,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "username": self.username,
            "password": self.password,
            "sender_email": self.sender_email,
            "use_ssl": self.use_ssl,
            "use_starttls": self.use_starttls,
            "smtp_username": self.smtp_username,
            "smtp_password": self.smtp_password,
            "use_tls": self.use_tls,
            "imap_or_pop_host": self.imap_or_pop_host,
            "imap_or_pop_port": self.imap_or_pop_port,
            "incoming_encryption": self.incoming_encryption,
            "smtp_encryption": self.smtp_encryption,
            "remember_password": self.remember_password,
            "require_spa": self.require_spa,
            "use_same_credentials": self.use_same_credentials,
            "director_and_owners": self.director_and_owners,
            "reminder_recipients": self.reminder_recipients,
            "reminder_subject_template": self.reminder_subject_template,
            "reminder_body_template": self.reminder_body_template,
            "escalation_subject_template": self.escalation_subject_template,
            "escalation_body_template": self.escalation_body_template,
        }


def _send_email_message(
    settings: EmailSettings,
    recipients: list[str],
    subject: str,
    body: str,
    attachment_paths: list[Path] | None = None,
    html_body: str | None = None,
) -> None:
    username = str(getattr(settings, "username", "") or getattr(settings, "smtp_username", "") or "")
    password = str(getattr(settings, "password", "") or getattr(settings, "smtp_password", "") or "")
    use_ssl = bool(getattr(settings, "use_ssl", False)) or str(
        getattr(settings, "smtp_encryption", "")
    ).casefold() in {"ssl/tls", "ssl", "tls"}
    use_starttls = bool(getattr(settings, "use_starttls", getattr(settings, "use_tls", True)))
    encryption = str(getattr(settings, "smtp_encryption", "") or "").casefold()
    if encryption in {"none", "no encryption"}:
        use_starttls = False

    message = EmailMessage()
    message["Subject"] = sanitize_email_subject(subject)
    message["From"] = settings.sender_email
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    if html_body:
        message.add_alternative(str(html_body), subtype="html")

    for path in attachment_paths or []:
        data = Path(path).read_bytes()
        message.add_attachment(data, maintype="application", subtype="octet-stream", filename=Path(path).name)

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if use_starttls:
            smtp.starttls(context=context)
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


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
        self.send_email = send_email
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

    def send_test_preview(
        self,
        rule: NotificationRule,
        payload: dict[str, str],
        recipient_email: str,
        idempotency_key: str,
    ) -> NotificationSendResult:
        recipient = str(recipient_email or "").strip()
        if not is_valid_email_address(recipient):
            raise ValueError("A valid test recipient email is required.")
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("Notification idempotency key is required.")

        event_type = f"{rule.event_type}.test"

        def finish(status: str, *, recipient_count: int = 0, error: str = "") -> NotificationSendResult:
            safe_error = _sanitize_error(error)
            if rule.id is not None:
                self.store.record_send_attempt(
                    event_type=event_type,
                    rule_id=rule.id,
                    idempotency_key=key,
                    recipient_count=recipient_count,
                    status=status,
                    error=safe_error,
                )
            return NotificationSendResult(event_type, rule.id, status, recipient_count, safe_error)

        validation_rule = replace(
            rule,
            active=True,
            recipients=[NotificationRecipient(email=recipient)],
        )
        blocking = [issue for issue in validate_notification_rule(validation_rule) if issue.blocking]
        if blocking:
            return finish("blocked", error=blocking[0].message)

        rendered = render_notification_templates(rule, payload)
        if rendered.unresolved_fields:
            return finish(
                "blocked",
                error=f"Missing template values: {', '.join(rendered.unresolved_fields)}.",
            )
        attachment_paths, attachment_error = _notification_attachment_paths(f"{rule.event_type}.test", payload)
        if attachment_error:
            return finish("blocked", recipient_count=1, error=attachment_error)
        try:
            self._deliver_message(
                [recipient],
                rendered.subject,
                rendered.plain_body,
                rendered.html_body,
                attachment_paths,
            )
        except Exception as exc:
            return finish("failed", recipient_count=1, error=str(exc))
        return finish("sent", recipient_count=1)

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

        rendered = render_notification_templates(rule, payload)
        subject = rendered.subject
        body = rendered.plain_body
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
            self._deliver_message(
                recipients,
                subject,
                body,
                rendered.html_body,
                attachment_paths,
            )
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

    def _deliver_message(
        self,
        recipients: list[str],
        subject: str,
        plain_body: str,
        html_body: str,
        attachment_paths: list[str],
    ) -> None:
        if self.send_email is None:
            parameters = inspect.signature(_send_email_message).parameters
            if "html_body" in parameters:
                _send_email_message(
                    self.email_settings,
                    recipients,
                    subject,
                    plain_body,
                    attachment_paths or None,
                    html_body=html_body,
                )
            elif attachment_paths:
                _send_email_message(self.email_settings, recipients, subject, plain_body, attachment_paths)
            else:
                _send_email_message(self.email_settings, recipients, subject, plain_body)
            return
        if attachment_paths:
            self.send_email(self.email_settings, recipients, subject, plain_body, attachment_paths)
            return
        self.send_email(self.email_settings, recipients, subject, plain_body)

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
