from onboarding_operations import (
    recipient_warning_text,
    reminder_send_estimate,
    split_and_validate_recipients,
    unknown_placeholder_actionable_message,
    validate_sender_email,
)
import onboarding_operations


class _ResultStub:
    def __init__(self, due_reminders: int, escalation_candidates: list[dict[str, str]], recipients: dict[str, list[str]]) -> None:
        self.counts = {"due_reminders": due_reminders}
        self.escalation_candidates = escalation_candidates
        self.recipients = recipients


def test_split_and_validate_recipients_returns_valid_and_invalid() -> None:
    valid, invalid = split_and_validate_recipients("ok@example.com, bad@@example, also@example.org")
    assert valid == ["ok@example.com", "also@example.org"]
    assert invalid == ["bad@@example"]


def test_recipient_warning_text_fails_closed_for_missing_or_malformed_recipients() -> None:
    assert recipient_warning_text("", channel_label="Reminder") == (
        "Reminder: Add at least one recipient email address before running reminders."
    )
    assert recipient_warning_text("bad@@example", channel_label="Escalation") == (
        "Escalation: Fix malformed addresses: bad@@example"
    )
    assert onboarding_operations.recipient_warning_text("ok@example.com", channel_label="Reminder") == ""


def test_validate_sender_email_delegates_to_shared_email_safety() -> None:
    assert validate_sender_email("sender@example.org") == (True, None)
    assert validate_sender_email("not-an-email") == (False, "invalid_format")


def test_reminder_send_estimate_counts_channels() -> None:
    stub = _ResultStub(
        due_reminders=3,
        escalation_candidates=[{"employee_name": "Pat"}],
        recipients={"reminder": ["coach@example.com"], "escalation": ["director@example.com"]},
    )
    estimate = reminder_send_estimate(stub)
    assert estimate == {"email_messages": 2, "in_app_messages": 3, "total_messages": 5}


def test_unknown_placeholder_actionable_message_contains_guidance() -> None:
    msg = unknown_placeholder_actionable_message({"reminder_body_template": {"weird_token"}})
    assert "Fix unknown placeholders before continuing." in msg
    assert "reminder_body_template" in msg
    assert "weird_token" in msg
