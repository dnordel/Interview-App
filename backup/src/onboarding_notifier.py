from __future__ import annotations

from email.message import EmailMessage
import smtplib
import ssl

from email_security import sanitize_email_subject
from onboarding_models import EmailSettings, resolve_smtp_password
from onboarding_scheduler import ReminderItem
from template_placeholders import missing_placeholder_keys, render_template


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
