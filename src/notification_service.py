from __future__ import annotations

import inspect
import base64
import ctypes
import imaplib
import poplib
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
import smtplib
import ssl
import sys
from typing import Any

from email_security import is_valid_email_address, sanitize_email_subject
from notification_models import NotificationCondition, NotificationRecipient, NotificationRule, NotificationSendResult
from notification_store import NotificationStore
from notification_templates import (
    render_notification_templates,
    validate_notification_rule,
)
from platform_services import USER_ARTIFACTS_DIR, atomic_write_json, safe_read_json


NOTIFICATION_RULES_PATH = USER_ARTIFACTS_DIR / "notification_rules.sqlite3"
EMAIL_ACCOUNT_SETTINGS_PATH = USER_ARTIFACTS_DIR / "email_account_settings.json"
HIRING_MANAGER_EMAIL_SETTINGS_PATH = USER_ARTIFACTS_DIR / "hiring_manager_email_account_settings.json"
NOTIFICATION_DIRECTORY_PATH = USER_ARTIFACTS_DIR / "notification_directory.json"
CANDIDATE_NOTIFICATION_TEST_EMAIL = "davidn@launchpadpreschool.com"
HIRING_MANAGER_EMAIL = "recruiting@launchpadpreschool.com"
EXECUTIVE_DIRECTOR_EMAIL = "deidre@launchpadpreschool.com"
DIRECTOR_EMAILS_BY_SCHOOL = {
    "hawthorne": "director@launchpadpreschoolHAW.com",
    "north long beach": "director@launchpadpreschoolNLB.com",
    "palmdale": "director@launchpadpreschoolPMD.com",
}
_SESSION_EMAIL_PASSWORDS: dict[str, tuple[str, str]] = {}


def candidate_notification_test_recipient(
    rule: NotificationRule,
    requested_email: str = "",
) -> str:
    """Return safe recipient for candidate-targeted notification tests, else blank."""
    targets_candidate = any(
        str(recipient.recipient_type or "").strip().casefold() == "role"
        and str(recipient.role_key or "").strip().casefold() == "candidate"
        for recipient in rule.recipients
    )
    if not targets_candidate:
        return ""
    recipient = str(requested_email or "").strip() or CANDIDATE_NOTIFICATION_TEST_EMAIL
    if not is_valid_email_address(recipient):
        raise ValueError("A valid candidate test recipient email is required.")
    return recipient


def missing_email_account_fields(settings: EmailSettings) -> tuple[str, ...]:
    """Return operator-facing names for required shared-account fields."""
    username = str(
        getattr(settings, "smtp_username", "") or getattr(settings, "username", "") or ""
    ).strip()
    password = str(
        getattr(settings, "smtp_password", "") or getattr(settings, "password", "") or ""
    )
    fields = (
        ("email address", str(getattr(settings, "sender_email", "") or "").strip()),
        (
            "incoming account type",
            str(getattr(settings, "account_type", "") or "").strip().upper()
            if str(getattr(settings, "account_type", "") or "").strip().upper() in {"IMAP", "POP3"}
            else "",
        ),
        ("incoming server", str(getattr(settings, "imap_or_pop_host", "") or "").strip()),
        ("SMTP server", str(getattr(settings, "smtp_host", "") or "").strip()),
        ("username", username),
        ("password", password),
    )
    return tuple(label for label, value in fields if not value)


def verify_email_account_connections(settings: EmailSettings) -> None:
    """Authenticate incoming and outgoing shared-account connections without sending."""
    missing = missing_email_account_fields(settings)
    if missing:
        raise ValueError(f"Missing shared email settings: {', '.join(missing)}.")
    username = str(settings.smtp_username or settings.username).strip()
    password = str(settings.smtp_password or settings.password)
    context = ssl.create_default_context()
    account_type = str(settings.account_type or "").strip().upper()
    if account_type == "IMAP":
        incoming = imaplib.IMAP4_SSL(
            settings.imap_or_pop_host,
            settings.imap_or_pop_port,
            ssl_context=context,
            timeout=30,
        )
        try:
            incoming.login(username, password)
        finally:
            incoming.logout()
    elif account_type == "POP3":
        incoming = poplib.POP3_SSL(
            settings.imap_or_pop_host,
            settings.imap_or_pop_port,
            timeout=30,
            context=context,
        )
        try:
            incoming.user(username)
            incoming.pass_(password)
        finally:
            incoming.quit()
    else:
        raise ValueError("Incoming account type is not supported.")

    encryption = str(settings.smtp_encryption or "STARTTLS").strip().casefold()
    if encryption in {"ssl/tls", "ssl", "tls"}:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30) as smtp:
            smtp.login(username, password)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if encryption in {"starttls", "start tls"}:
            smtp.starttls(context=context)
        smtp.login(username, password)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect_secret(value: str) -> str:
    clean = str(value or "")
    if not clean:
        return ""
    if not sys.platform.startswith("win"):
        raise OSError("Windows DPAPI is required to remember SMTP passwords.")
    raw = clean.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob), "LPL SMTP", None, None, None, 1, ctypes.byref(output_blob)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    text = str(value or "")
    if not text.startswith("dpapi:"):
        return text
    if not sys.platform.startswith("win"):
        return ""
    try:
        raw = base64.b64decode(text[6:], validate=True)
    except (ValueError, TypeError):
        return ""
    buffer = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob)
    ):
        return ""
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


@dataclass(frozen=True)
class NotificationDirectory:
    hiring_manager: str
    executive_director: str
    hr_manager: str
    payroll: str
    directors: dict[str, str]
    director_names: dict[str, str]
    office_managers: dict[str, str]
    onboarding_guide_path: str

    @classmethod
    def defaults(cls) -> "NotificationDirectory":
        return cls(
            hiring_manager=HIRING_MANAGER_EMAIL,
            executive_director=EXECUTIVE_DIRECTOR_EMAIL,
            hr_manager=HIRING_MANAGER_EMAIL,
            payroll="payroll@launchpadpreschool.com",
            directors=dict(DIRECTOR_EMAILS_BY_SCHOOL),
            director_names={
                "hawthorne": "Netsi",
                "palmdale": "Edith",
                "north long beach": "Claudia",
                "long beach": "Claudia",
            },
            office_managers={
                "hawthorne": "admin-haw@launchpadpreschool.com",
                "palmdale": "admin-pmd@launchpadpreschool.com",
                "north long beach": "admin-nlb@launchpadpreschool.com",
                "long beach": "admin-nlb@launchpadpreschool.com",
            },
            onboarding_guide_path=(
                r"C:\Users\Dnord\Dropbox (Old)\Dropbox\All School Admin\LPL New Employee Onboarding Guide v1.3.pdf"
            ),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "NotificationDirectory":
        defaults = cls.defaults()
        source = payload if isinstance(payload, dict) else {}
        directors = dict(defaults.directors)
        directors.update({str(k).casefold(): str(v).strip() for k, v in dict(source.get("directors") or {}).items()})
        director_names = dict(defaults.director_names)
        director_names.update(
            {str(k).casefold(): str(v).strip() for k, v in dict(source.get("director_names") or {}).items()}
        )
        offices = dict(defaults.office_managers)
        offices.update({str(k).casefold(): str(v).strip() for k, v in dict(source.get("office_managers") or {}).items()})
        return cls(
            hiring_manager=str(source.get("hiring_manager") or defaults.hiring_manager).strip(),
            executive_director=str(source.get("executive_director") or defaults.executive_director).strip(),
            hr_manager=str(source.get("hr_manager") or defaults.hr_manager).strip(),
            payroll=str(source.get("payroll") or defaults.payroll).strip(),
            directors=directors,
            director_names=director_names,
            office_managers=offices,
            onboarding_guide_path=str(
                source.get("onboarding_guide_path") or defaults.onboarding_guide_path
            ).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hiring_manager": self.hiring_manager,
            "executive_director": self.executive_director,
            "hr_manager": self.hr_manager,
            "payroll": self.payroll,
            "directors": dict(self.directors),
            "director_names": dict(self.director_names),
            "office_managers": dict(self.office_managers),
            "onboarding_guide_path": self.onboarding_guide_path,
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
    password_storage: str = "windows_user"
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
            password_storage=str(source.get("password_storage", "windows_user") or "windows_user"),
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
            "password_storage": self.password_storage,
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


def notification_conditions_match(
    conditions: list[NotificationCondition],
    payload: dict[str, str],
) -> bool:
    for condition in conditions:
        actual = str(payload.get(str(condition.field), "")).strip().casefold()
        operator = str(condition.operator or "equals").strip().casefold()
        expected = str(condition.value or "").strip().casefold()
        if operator == "equals":
            matches = actual == expected
        elif operator == "not_equals":
            matches = actual != expected
        elif operator == "in":
            values = {item.strip() for item in expected.split(",") if item.strip()}
            matches = actual in values
        elif operator == "not_in":
            values = {item.strip() for item in expected.split(",") if item.strip()}
            matches = actual not in values
        elif operator == "contains":
            matches = expected in actual
        elif operator == "not_contains":
            matches = expected not in actual
        elif operator == "is_blank":
            matches = not actual
        elif operator == "is_not_blank":
            matches = bool(actual)
        elif operator in {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}:
            try:
                actual_number = Decimal(re.sub(r"[$,%\s]", "", actual))
                expected_number = Decimal(re.sub(r"[$,%\s]", "", expected))
            except InvalidOperation:
                matches = False
            else:
                matches = {
                    "greater_than": actual_number > expected_number,
                    "greater_than_or_equal": actual_number >= expected_number,
                    "less_than": actual_number < expected_number,
                    "less_than_or_equal": actual_number <= expected_number,
                }[operator]
        else:
            matches = False
        if not matches:
            return False
    return True


class NotificationService:
    def __init__(
        self,
        *,
        store: NotificationStore | None = None,
        email_settings: EmailSettings | None = None,
        send_email: Callable[..., Any] | None = None,
        current_date: Callable[[], date] | None = None,
        directory: NotificationDirectory | None = None,
        hiring_manager_email_settings: EmailSettings | None = None,
    ) -> None:
        self.store = store or NotificationStore(NOTIFICATION_RULES_PATH)
        self.email_settings = email_settings or EmailSettings()
        self.send_email = send_email
        self.current_date = current_date or date.today
        self.directory = directory or NotificationDirectory.defaults()
        self.hiring_manager_email_settings = hiring_manager_email_settings or self.email_settings

    def activate_ready_system_rules(self) -> int:
        changed = 0
        for rule in self.store.list_rules():
            if not rule.system_rule or rule.active or rule.user_disabled or rule.id is None:
                continue
            settings = (
                self.hiring_manager_email_settings
                if rule.sender_account == "hiring_manager"
                else self.email_settings
            )
            if not settings.smtp_host or not is_valid_email_address(settings.sender_email):
                continue
            if rule.required_attachment_key == "onboarding_guide_path":
                if not Path(self.directory.onboarding_guide_path).is_file():
                    continue
            payload = {"school": "Hawthorne", "candidate_email": "candidate@example.org"}
            recipients, _, error = resolve_notification_recipients(
                rule, payload, directory=self.directory
            )
            if error or not recipients or any(not is_valid_email_address(email) for email in recipients):
                continue
            self.store.activate_system_rule(rule.id)
            changed += 1
        return changed

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

    def render_candidate_event_preview(
        self,
        event_type: str,
        payload: dict[str, str],
    ) -> str:
        """Render same active candidate-targeted template used for event delivery."""
        rules = [rule for rule in self.store.list_rules(str(event_type).strip()) if rule.active]
        candidate_rules = [
            rule
            for rule in rules
            if any(
                recipient.active
                and str(recipient.recipient_type or "").strip().casefold() == "role"
                and str(recipient.role_key or "").strip().casefold() == "candidate"
                for recipient in rule.recipients
            )
        ]
        if not candidate_rules:
            raise ValueError("No active candidate notification template is configured for this event.")
        rendered = render_notification_templates(candidate_rules[0], payload)
        if rendered.unresolved_fields:
            raise ValueError(
                f"Candidate notification template is missing values: {', '.join(rendered.unresolved_fields)}."
            )
        return f"Subject: {rendered.subject}\n\n{rendered.plain_body}"

    def send_test(
        self,
        rule_id: int,
        payload: dict[str, str],
        idempotency_key: str,
    ) -> NotificationSendResult:
        rule = self.store.get_rule(int(rule_id))
        candidate_test_email = candidate_notification_test_recipient(rule)
        if candidate_test_email:
            rule = replace(
                rule,
                recipients=[
                    NotificationRecipient(
                        email=candidate_test_email,
                        name="Candidate test recipient",
                        role_label="Candidate test recipient",
                    )
                ],
            )
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
        recipient = candidate_notification_test_recipient(rule, recipient_email)
        if not recipient:
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

        if missing_email_account_fields(self.email_settings):
            return finish("blocked", error="Shared email account settings are incomplete.")

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
                f"[THIS IS A TEST] {rendered.subject}",
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
        payload = dict(payload)
        if (
            str(event_type).removesuffix(".test") == "offer.accepted"
            and rule.required_attachment_key == "onboarding_guide_path"
            and not str(payload.get("onboarding_guide_path", "") or "").strip()
        ):
            payload["onboarding_guide_path"] = self.directory.onboarding_guide_path
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

        recipients, _summary, recipient_error = resolve_notification_recipients(
            rule, payload, directory=self.directory
        )
        sender_settings = (
            self.hiring_manager_email_settings
            if rule.sender_account == "hiring_manager"
            else self.email_settings
        )
        blocked_error = recipient_error or self._blocked_reason(
            rule, recipients, payload, settings=sender_settings
        )
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
        subject = (
            f"[THIS IS A TEST] {rendered.subject}"
            if str(event_type).endswith(".test")
            else rendered.subject
        )
        body = rendered.plain_body
        required_attachment = str(rule.required_attachment_key or "").strip()
        if required_attachment and not str(payload.get(required_attachment, "") or "").strip():
            attachment_paths, attachment_error = [], "Required notification attachment is missing."
        else:
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
                settings=sender_settings,
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
        settings: EmailSettings | None = None,
    ) -> None:
        active_settings = settings or self.email_settings
        if self.send_email is None:
            parameters = inspect.signature(_send_email_message).parameters
            if "html_body" in parameters:
                _send_email_message(
                    active_settings,
                    recipients,
                    subject,
                    plain_body,
                    attachment_paths or None,
                    html_body=html_body,
                )
            elif attachment_paths:
                _send_email_message(active_settings, recipients, subject, plain_body, attachment_paths)
            else:
                _send_email_message(active_settings, recipients, subject, plain_body)
            return
        if attachment_paths:
            self.send_email(active_settings, recipients, subject, plain_body, attachment_paths)
            return
        self.send_email(active_settings, recipients, subject, plain_body)

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
        if not notification_conditions_match(rule.conditions, payload):
            return "condition_not_met"
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

    def _blocked_reason(
        self,
        rule: NotificationRule,
        recipients: list[str],
        payload: dict[str, str],
        *,
        settings: EmailSettings | None = None,
    ) -> str:
        if not recipients:
            return "No active recipients configured."
        active_settings = settings or self.email_settings
        if not active_settings.smtp_host or not active_settings.sender_email:
            return "SMTP settings are incomplete."
        invalid = [email for email in recipients if not is_valid_email_address(email)]
        if invalid:
            return "One or more recipient addresses are invalid."
        missing = sorted(_missing_template_keys([rule.subject_template, rule.body_template], payload))
        if missing:
            return f"Missing values for placeholders: {', '.join(missing)}"
        return ""


class StaffingNotificationScheduler:
    """Clock-aware local staffing date trigger scanner."""

    def __init__(
        self,
        *,
        staffing_store: Any,
        notification_service: Any,
        now: Callable[[], datetime] | None = None,
        rollout_date: date | None = None,
        candidate_contact_resolver: Callable[[str, str], dict[str, str]] | None = None,
    ) -> None:
        self.staffing_store = staffing_store
        self.notification_service = notification_service
        self.now = now or datetime.now
        self.rollout_date = rollout_date or self.now().date()
        self.candidate_contact_resolver = candidate_contact_resolver

    def run(self) -> list[NotificationSendResult]:
        from staffing_service import staffing_notification_payload

        current = self.now()
        today = current.date()
        people = {person.id: person for person in self.staffing_store.list_people()}
        results: list[NotificationSendResult] = []
        for assignment in self.staffing_store.list_assignments():
            if not self._record_in_rollout(assignment.updated_at):
                continue
            person = people.get(assignment.person_id)
            payload = staffing_notification_payload(assignment, person)
            contact = (
                self.candidate_contact_resolver(assignment.person_name, assignment.school)
                if self.candidate_contact_resolver is not None
                else {}
            )
            directory = getattr(
                self.notification_service, "directory", NotificationDirectory.defaults()
            )
            payload.update(
                {
                    "candidate_email": str(
                        contact.get("email") or getattr(person, "email", "") or ""
                    ),
                    "honorific": str(
                        contact.get("honorific") or getattr(person, "honorific", "") or "Ms."
                    ),
                    "director_name": directory.director_names.get(
                        str(assignment.school).strip().casefold(), "Director"
                    ),
                }
            )
            if assignment.start_date == today.isoformat():
                results.extend(
                    self.notification_service.emit_event(
                        "employment.start.today",
                        payload,
                        f"staffing:{assignment.id}:start:{today.isoformat()}",
                    )
                )
            final_day = str(getattr(person, "final_working_day", "") or assignment.final_working_day)
            if final_day == today.isoformat() and current.time() >= time(12, 0):
                results.extend(
                    self.notification_service.emit_event(
                        "employment.last_day",
                        payload,
                        f"staffing:{assignment.id}:last-day:{today.isoformat()}",
                    )
                )
            permit_event = self._permit_event(assignment, person, today)
            if permit_event:
                results.extend(
                    self.notification_service.emit_event(
                        permit_event,
                        payload,
                        f"staffing:{assignment.id}:{permit_event}",
                    )
                )
        return results

    def _record_in_rollout(self, updated_at: str) -> bool:
        try:
            updated = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")).date()
        except ValueError:
            return False
        return updated >= self.rollout_date

    @staticmethod
    def _permit_event(assignment: Any, person: Any, today: date) -> str:
        if person is None or not assignment.start_date:
            return ""
        role = str(getattr(person, "role", "") or assignment.position_type).casefold().replace("_", " ")
        eligible_role = "teacher" in role or "aide" in role or "assistant director" in role
        permit_status = str(getattr(person, "permit_status", "") or assignment.permit_status)
        if not eligible_role or permit_status not in {"unknown", "no_permit_or_application"}:
            return ""
        try:
            days = (today - date.fromisoformat(assignment.start_date)).days
        except ValueError:
            return ""
        if days >= 90:
            return "permit.escalation.90d"
        if days >= 50:
            return "permit.eligible.50d"
        return ""


def resolve_notification_recipients(
    rule: NotificationRule,
    payload: dict[str, str],
    *,
    directory: NotificationDirectory | None = None,
) -> tuple[list[str], str, str]:
    active_directory = directory or NotificationDirectory.defaults()
    recipients: list[str] = []
    summary_parts: list[str] = []
    for recipient in rule.recipients:
        if not recipient.active:
            continue
        resolved, summary, error = _resolve_notification_recipient(recipient, payload, active_directory)
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
    directory: NotificationDirectory,
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
        return directory.hiring_manager, "hiring manager", ""
    if role_key == "executive_director":
        return directory.executive_director, "executive director", ""
    if role_key == "hr_manager":
        return directory.hr_manager, "HR manager", ""
    if role_key == "payroll":
        return directory.payroll, "payroll", ""
    if role_key == "candidate":
        candidate_email = str(payload.get("candidate_email", "") or payload.get("email", "")).strip()
        if not is_valid_email_address(candidate_email):
            return "", "", "Candidate recipient requires a valid candidate email."
        return candidate_email, "candidate", ""
    if role_key == "director":
        school_key = str(payload.get("school", "")).strip().casefold()
        director_email = directory.directors.get(school_key)
        if not director_email:
            return "", "", "Director recipient requires a supported school."
        return director_email, "school director", ""
    if role_key == "office_manager":
        school_key = str(payload.get("school", "")).strip().casefold()
        office_email = directory.office_managers.get(school_key)
        if not office_email:
            return "", "", "Office Manager recipient requires a supported school."
        return office_email, "office manager", ""
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
    settings = EmailSettings.from_dict(source if isinstance(source, dict) else {})
    key = str(Path(path).expanduser().resolve())
    if settings.password_storage == "shared_config":
        return settings
    if settings.remember_password:
        return replace(
            settings,
            password=unprotect_secret(settings.password),
            smtp_password=unprotect_secret(settings.smtp_password),
        )
    session_password, session_smtp_password = _SESSION_EMAIL_PASSWORDS.get(key, ("", ""))
    return replace(settings, password=session_password, smtp_password=session_smtp_password)


def migrate_legacy_onboarding_email_account(
    *,
    legacy_path: Path,
    shared_path: Path = EMAIL_ACCOUNT_SETTINGS_PATH,
) -> bool:
    """Copy a legacy onboarding SMTP account once, never replacing shared settings."""
    shared_payload = safe_read_json(Path(shared_path), default={}, expected_type=dict)
    shared_email = dict(shared_payload.get("email") or {}) if isinstance(shared_payload, dict) else {}
    meaningful_fields = {
        "account_label",
        "display_name",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "username",
        "password",
        "sender_email",
        "imap_or_pop_host",
    }
    if any(str(shared_email.get(field) or "").strip() for field in meaningful_fields):
        return False

    legacy_payload = safe_read_json(Path(legacy_path), default={}, expected_type=dict)
    legacy_email = dict(legacy_payload.get("email") or {}) if isinstance(legacy_payload, dict) else {}
    legacy = EmailSettings.from_dict(legacy_email)
    if not legacy.smtp_host or not legacy.sender_email:
        return False
    save_email_account_settings(
        replace(legacy, remember_password=True, password_storage="shared_config"),
        Path(shared_path),
    )
    return True


def save_email_account_settings(settings: EmailSettings, path: Path = EMAIL_ACCOUNT_SETTINGS_PATH) -> None:
    target = Path(path)
    key = str(target.expanduser().resolve())
    payload = settings.to_dict()
    if settings.password_storage == "shared_config":
        _SESSION_EMAIL_PASSWORDS.pop(key, None)
        atomic_write_json(target, {"email": payload}, indent=2, ensure_ascii=False)
        return
    if settings.remember_password:
        payload["password"] = protect_secret(settings.password)
        payload["smtp_password"] = protect_secret(settings.smtp_password)
        _SESSION_EMAIL_PASSWORDS.pop(key, None)
    else:
        _SESSION_EMAIL_PASSWORDS[key] = (settings.password, settings.smtp_password)
        payload["password"] = ""
        payload["smtp_password"] = ""
    atomic_write_json(target, {"email": payload}, indent=2, ensure_ascii=False)


def load_notification_directory(path: Path = NOTIFICATION_DIRECTORY_PATH) -> NotificationDirectory:
    payload = safe_read_json(Path(path), default={}, expected_type=dict)
    return NotificationDirectory.from_dict(payload if isinstance(payload, dict) else {})


def save_notification_directory(
    directory: NotificationDirectory,
    path: Path = NOTIFICATION_DIRECTORY_PATH,
) -> None:
    for email in (
        directory.hiring_manager,
        directory.executive_director,
        directory.hr_manager,
        directory.payroll,
        *directory.directors.values(),
        *directory.office_managers.values(),
    ):
        if not is_valid_email_address(email):
            raise ValueError("Notification directory contains an invalid email address.")
    atomic_write_json(Path(path), directory.to_dict(), indent=2, ensure_ascii=False)


def resolve_onboarding_role_recipient(
    directory: NotificationDirectory,
    *,
    school: str,
    role: str,
    director_resolver: Callable[[str], Any] | None = None,
) -> str:
    school_name = str(school or "").strip()
    school_key = school_name.casefold()
    role_key = str(role or "").strip().casefold()
    if role_key == "director":
        if director_resolver is not None:
            director_resolver(school_name)
        return str(directory.directors.get(school_key) or "").strip()
    if role_key == "office manager":
        return str(directory.office_managers.get(school_key) or "").strip()
    if role_key == "payroll":
        return str(directory.payroll or "").strip()
    if role_key in {"admin", "hr", "hr manager"}:
        return str(directory.hr_manager or "").strip()
    return ""


def send_onboarding_reminder_digest(
    settings: EmailSettings,
    message: Any,
    *,
    rule_store: NotificationStore | None = None,
) -> None:
    school = str(message.school or "").strip()
    role = str(message.role or "").strip()
    recipient = str(message.recipient or "").strip()
    task_count = len(tuple(message.task_ids))
    subject = f"{school} onboarding tasks due — {role}"
    body = (
        f"{task_count} onboarding tasks require attention. "
        "Open the Onboarding Tasks page for authorized details."
    )
    if rule_store is not None:
        rules = [rule for rule in rule_store.list_rules("onboarding.digest.due") if rule.active]
        if not rules:
            raise ValueError("Enable the Onboarding due digest rule in Notifications before sending.")
        rendered = render_notification_templates(rules[0], {
            "school": school, "owner_role": role, "task_count": str(task_count),
        })
        subject, body = rendered.subject, rendered.plain_body
    _send_email_message(settings, [recipient], subject, body)


def notification_service_from_email_account_settings(
    *,
    settings_path: Path = EMAIL_ACCOUNT_SETTINGS_PATH,
    store_path: Path = NOTIFICATION_RULES_PATH,
    hiring_manager_settings_path: Path = HIRING_MANAGER_EMAIL_SETTINGS_PATH,
    directory_path: Path = NOTIFICATION_DIRECTORY_PATH,
) -> NotificationService:
    hiring_settings = load_email_account_settings(hiring_manager_settings_path)
    if not hiring_settings.sender_email:
        hiring_settings = replace(hiring_settings, sender_email=HIRING_MANAGER_EMAIL)
    return NotificationService(
        store=NotificationStore(store_path),
        email_settings=load_email_account_settings(settings_path),
        hiring_manager_email_settings=hiring_settings,
        directory=load_notification_directory(directory_path),
    )


def _missing_template_keys(templates: list[str], payload: dict[str, str]) -> set[str]:
    available = {str(key) for key in payload}
    found: set[str] = set()
    for template in templates:
        found.update(match.group(1) for match in re.finditer(r"{([A-Za-z_][A-Za-z0-9_]*)}", str(template or "")))
    return found - available


def _sanitize_error(value: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", str(value or ""))
    text = re.sub(r"(?i)(password|token|secret)=\S+", r"\1=[REDACTED]", text)
    return text[:300]
