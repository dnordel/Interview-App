from onboarding_send_guardrails import (
    reminder_send_estimate,
    split_and_validate_recipients,
    unknown_placeholder_actionable_message,
)


class _ResultStub:
    def __init__(self, due_reminders: int, escalation_candidates: list[dict[str, str]], recipients: dict[str, list[str]]) -> None:
        self.counts = {"due_reminders": due_reminders}
        self.escalation_candidates = escalation_candidates
        self.recipients = recipients


def test_split_and_validate_recipients_returns_valid_and_invalid() -> None:
    valid, invalid = split_and_validate_recipients("ok@example.com, bad@@example, also@example.org")
    assert valid == ["ok@example.com", "also@example.org"]
    assert invalid == ["bad@@example"]


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
