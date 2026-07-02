from __future__ import annotations

from pathlib import Path

import pytest

from notification_models import NotificationRecipient, NotificationRule
from notification_service import NotificationService
from notification_store import NotificationStore
from onboarding_operations import EmailSettings


def _settings() -> EmailSettings:
    return EmailSettings(sender_email="sender@example.org", smtp_host="smtp.example.org")


def test_notification_rule_crud_supports_multiple_recipients(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    saved = store.save_rule(
        NotificationRule(
            event_type="staffing.assignment.need_now",
            label="Need now",
            subject_template="Need now: {position_name}",
            body_template="{school} needs {position_name}.",
            recipients=[
                NotificationRecipient(name="Hiring Manager", email="hm@example.org", role_label="Hiring manager"),
                NotificationRecipient(name="Director", email="director@example.org", role_label="Director"),
            ],
        )
    )

    rules = store.list_rules("staffing.assignment.need_now")
    assert len(rules) == 1
    assert rules[0].id == saved.id
    assert [recipient.email for recipient in rules[0].recipients] == ["hm@example.org", "director@example.org"]


def test_notification_service_sends_matching_rule_once_per_idempotency_key(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="Offer accepted",
            subject_template="Accepted: {candidate_name}",
            body_template="{candidate_name} accepted {position}.",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    sent: list[tuple[list[str], str, str]] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda settings, recipients, subject, body: sent.append((recipients, subject, body)),
    )

    first = service.emit_event(
        "offer.accepted",
        {"candidate_name": "Jane Doe", "position": "Teacher"},
        "offer-1-accepted",
    )
    second = service.emit_event(
        "offer.accepted",
        {"candidate_name": "Jane Doe", "position": "Teacher"},
        "offer-1-accepted",
    )

    assert first[0].status == "sent"
    assert second[0].status == "duplicate"
    assert sent == [(["director@example.org"], "Accepted: Jane Doe", "Jane Doe accepted Teacher.")]


def test_notification_service_blocks_unknown_placeholders(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="Broken template",
            subject_template="Accepted: {missing}",
            body_template="Body",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    service = NotificationService(store=store, email_settings=_settings(), send_email=lambda *_: None)

    results = service.emit_event("offer.accepted", {"candidate_name": "Jane Doe"}, "offer-2-accepted")

    assert [result.status for result in results] == ["blocked"]
    assert all("@" not in result.error for result in results)


def test_notification_store_rejects_invalid_rule_email_before_save(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    with pytest.raises(ValueError, match="Invalid recipient email"):
        store.save_rule(
            NotificationRule(
                event_type="offer.accepted",
                label="Bad recipient",
                subject_template="Accepted",
                body_template="Body",
                recipients=[NotificationRecipient(email="not-an-email")],
            )
        )
