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


def test_notification_rule_crud_persists_date_offset_trigger_and_delete(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    saved = store.save_rule(
        NotificationRule(
            event_type="staffing.assignment.coming",
            label="Start date reminder",
            subject_template="Start soon: {person_name}",
            body_template="{person_name} starts on {start_date}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="date_offset",
            date_field="start_date",
            offset_days=-3,
        )
    )

    [rule] = store.list_rules("staffing.assignment.coming")
    assert rule.trigger_timing == "date_offset"
    assert rule.date_field == "start_date"
    assert rule.offset_days == -3

    store.delete_rule(saved.id or 0)

    assert store.list_rules("staffing.assignment.coming") == []


def test_notification_store_seeds_offer_generated_default_rule(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    store.ensure_default_rules()

    [rule] = store.list_rules("offer.generated")
    assert rule.label == "Leadership: offer generated"
    assert rule.active is False
    assert rule.trigger_timing == "event"
    assert "{candidate_name}" in rule.subject_template
    assert "{start_date}" in rule.body_template


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


def test_notification_service_sends_date_offset_rule_only_on_due_date(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="staffing.assignment.coming",
            label="Start date reminder",
            subject_template="Start soon: {person_name}",
            body_template="{person_name} starts on {start_date}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="date_offset",
            date_field="start_date",
            offset_days=-3,
        )
    )
    sent: list[tuple[list[str], str, str]] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda settings, recipients, subject, body: sent.append((recipients, subject, body)),
        current_date=lambda: __import__("datetime").date(2026, 7, 7),
    )

    early = service.emit_event(
        "staffing.assignment.coming",
        {"person_name": "Jane Doe", "start_date": "2026-07-11"},
        "start-reminder",
    )
    due = service.emit_event(
        "staffing.assignment.coming",
        {"person_name": "Jane Doe", "start_date": "2026-07-10"},
        "start-reminder-due",
    )

    assert early[0].status == "not_due"
    assert due[0].status == "sent"
    assert sent == [(["director@example.org"], "Start soon: Jane Doe", "Jane Doe starts on 2026-07-10.")]


def test_notification_service_queues_future_date_offset_and_sends_when_due(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.generated",
            label="Start date reminder",
            subject_template="Start soon: {candidate_name}",
            body_template="{candidate_name} starts on {start_date}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="date_offset",
            date_field="start_date",
            offset_days=-3,
        )
    )
    sent: list[tuple[list[str], str, str]] = []
    today = __import__("datetime").date(2026, 7, 1)
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda settings, recipients, subject, body: sent.append((recipients, subject, body)),
        current_date=lambda: today,
    )

    queued = service.emit_event(
        "offer.generated",
        {"candidate_name": "Jane Doe", "start_date": "2026-07-10"},
        "offer-1-generated",
    )

    assert queued[0].status == "not_due"
    assert sent == []

    today = __import__("datetime").date(2026, 7, 7)
    due = service.run_due_notifications()

    assert due[0].status == "sent"
    assert sent == [(["director@example.org"], "Start soon: Jane Doe", "Jane Doe starts on 2026-07-10.")]
    assert service.run_due_notifications() == []
    assert sent == [(["director@example.org"], "Start soon: Jane Doe", "Jane Doe starts on 2026-07-10.")]


def test_notification_service_can_schedule_from_offer_generated_date(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.generated",
            label="Generated follow up",
            subject_template="Offer generated: {candidate_name}",
            body_template="Offer was generated on {generated_date}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            trigger_timing="date_offset",
            date_field="generated_date",
            offset_days=2,
        )
    )
    sent: list[tuple[list[str], str, str]] = []
    today = __import__("datetime").date(2026, 7, 5)
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda settings, recipients, subject, body: sent.append((recipients, subject, body)),
        current_date=lambda: today,
    )

    queued = service.emit_event(
        "offer.generated",
        {"candidate_name": "Jane Doe", "generated_date": "2026-07-05"},
        "offer-1-generated",
    )

    assert queued[0].status == "not_due"
    today = __import__("datetime").date(2026, 7, 7)
    due = service.run_due_notifications()

    assert due[0].status == "sent"
    assert sent == [(["director@example.org"], "Offer generated: Jane Doe", "Offer was generated on 2026-07-05.")]


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
