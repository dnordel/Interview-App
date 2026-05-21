from __future__ import annotations

import re

from email_security import sender_email_error_reason


_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


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

def reminder_send_estimate(result) -> dict[str, int]:
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
