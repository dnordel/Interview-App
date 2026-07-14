from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from notification_models import NotificationRecipient, NotificationRule
import notification_service
from notification_service import NotificationService, resolve_notification_recipients
from notification_store import NotificationStore
from onboarding_operations import EmailSettings


def _settings() -> EmailSettings:
    return EmailSettings(sender_email="sender@example.org", smtp_host="smtp.example.org")


def test_notification_sender_accepts_onboarding_email_settings_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            calls.append(("connect", (host, port, timeout)))

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def starttls(self, *, context: object) -> None:
            calls.append(("starttls", bool(context)))

        def login(self, username: str, password: str) -> None:
            calls.append(("login", (username, password)))

        def send_message(self, message: object) -> None:
            calls.append(("send", message["Subject"]))

    monkeypatch.setattr(notification_service.smtplib, "SMTP", FakeSmtp)
    settings = EmailSettings(
        sender_email="sender@example.org",
        smtp_host="smtp.example.org",
        smtp_username="user",
        smtp_password="secret",
        use_tls=True,
    )

    notification_service._send_email_message(settings, ["to@example.org"], "Hello\r\nWorld", "Body")

    assert calls[:3] == [
        ("connect", ("smtp.example.org", 587, 30)),
        ("starttls", True),
        ("login", ("user", "secret")),
    ]
    assert calls[3] == ("send", "Hello World")


def test_notification_sender_builds_plain_and_html_alternatives(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[object] = []

    class FakeSmtp:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def starttls(self, **_kwargs: object) -> None:
            pass

        def send_message(self, message: object) -> None:
            messages.append(message)

    monkeypatch.setattr(notification_service.smtplib, "SMTP", FakeSmtp)

    notification_service._send_email_message(
        _settings(),
        ["to@example.org"],
        "Hello",
        "Hello Alex",
        html_body="<p>Hello <strong>Alex</strong></p>",
    )

    message = messages[0]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Hello Alex"
    assert "<strong>Alex</strong>" in message.get_body(preferencelist=("html",)).get_content()


def test_notification_service_sends_current_draft_to_explicit_test_recipient_without_unsaved_audit(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    sent: list[tuple[list[str], str, str]] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda _settings, recipients, subject, body: sent.append((recipients, subject, body)),
    )
    draft = NotificationRule(
        event_type="custom.preview",
        label="Unsaved",
        subject_template="Hello {person_name}",
        body_template="Hi **{person_name}**",
        recipients=[],
        active=False,
    )

    result = service.send_test_preview(
        draft,
        {"person_name": "Alex"},
        "tester@example.org",
        "draft-preview-1",
    )

    assert result.status == "sent"
    assert sent == [(["tester@example.org"], "Hello Alex", "Hi Alex")]
    assert store.list_audit() == []


def test_notification_service_saved_draft_test_blocks_invalid_template_and_audits(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="custom.preview",
            label="Saved",
            subject_template="Hello {person_name}",
            body_template="Body",
            recipients=[],
            active=False,
        )
    )
    service = NotificationService(store=store, email_settings=_settings(), send_email=lambda *_args: None)

    result = service.send_test_preview(
        replace(saved, body_template="Unknown {secret_token}"),
        {"person_name": "Alex"},
        "tester@example.org",
        "saved-preview-invalid",
    )

    assert result.status == "blocked"
    assert "Unknown template variables" in result.error
    [audit] = store.list_audit(saved.id, limit=1)
    assert audit["status"] == "blocked"
    assert "tester@example.org" not in audit["error"]


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


def test_notification_store_blocks_incomplete_enabled_rule_but_allows_disabled_draft(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    incomplete = NotificationRule(
        event_type="custom.reminder",
        label="Draft",
        subject_template="",
        body_template="",
        recipients=[],
        active=True,
    )

    with pytest.raises(ValueError, match="Subject template"):
        store.save_rule(incomplete)

    saved = store.save_rule(NotificationRule(**{**incomplete.__dict__, "active": False}))
    assert saved.active is False


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

    events = {rule.event_type: rule for rule in store.list_rules()}
    assert {
        "staffing.assignment.need_now",
        "staffing.assignment.coming",
        "staffing.assignment.filled",
        "staffing.assignment.replace",
        "staffing.assignment.not_needed",
        "staffing.permit.updated",
        "offer.generated",
        "offer.approved",
        "offer.accepted",
        "custom.reminder",
    } <= set(events)
    assert events["offer.generated"].label == "Leadership: offer generated"
    assert events["offer.generated"].active is False
    assert events["offer.generated"].trigger_timing == "event"
    assert "{candidate_name}" in events["offer.generated"].subject_template
    assert "{start_date}" in events["offer.generated"].body_template
    assert [(recipient.recipient_type, recipient.role_key) for recipient in events["staffing.assignment.created"].recipients] == [
        ("role", "hiring_manager"),
        ("role", "director"),
    ]
    assert "{reply_by_date}" in events["offer.approved"].body_template
    assert "Offer of Employment - Launch Pad Learning {school_code}" == events["offer.approved"].subject_template
    assert [(recipient.recipient_type, recipient.role_key) for recipient in events["offer.approved"].recipients] == [
        ("role", "candidate")
    ]
    assert [(recipient.recipient_type, recipient.role_key) for recipient in events["offer.accepted"].recipients] == [
        ("role", "candidate"),
        ("role", "director"),
        ("role", "executive_director"),
    ]

    customized = store.save_rule(
        NotificationRule(
            id=events["staffing.assignment.need_now"].id,
            event_type="staffing.assignment.need_now",
            label="Custom existing",
            subject_template="Custom {position_name}",
            body_template="Custom body",
            recipients=events["staffing.assignment.need_now"].recipients,
            active=True,
        )
    )
    store.ensure_default_rules()
    [after] = store.list_rules("staffing.assignment.need_now")
    assert after.id == customized.id
    assert after.label == "Custom existing"
    assert after.active is True


def test_notification_store_backfills_recipients_only_for_untouched_defaults(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.ensure_default_rules()
    need_now = store.list_rules("staffing.assignment.need_now")[0]
    store.save_rule(
        NotificationRule(
            id=need_now.id,
            event_type=need_now.event_type,
            label=need_now.label,
            subject_template=need_now.subject_template,
            body_template=need_now.body_template,
            recipients=[],
            active=need_now.active,
        )
    )
    accepted = store.list_rules("offer.accepted")[0]
    customized = store.save_rule(
        NotificationRule(
            id=accepted.id,
            event_type=accepted.event_type,
            label="Custom offer accepted",
            subject_template=accepted.subject_template,
            body_template=accepted.body_template,
            recipients=[],
            active=False,
        )
    )

    store.ensure_default_rules()

    [backfilled] = store.list_rules("staffing.assignment.need_now")
    [custom_after] = store.list_rules("offer.accepted")
    assert [recipient.role_key for recipient in backfilled.recipients] == ["hiring_manager", "director"]
    assert custom_after.id == customized.id
    assert custom_after.recipients == []


def test_notification_store_lists_audit_and_scheduled_notifications(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="custom.reminder",
            label="Reminder",
            subject_template="Reminder",
            body_template="Body",
            recipients=[NotificationRecipient(email="director@example.org")],
        )
    )
    assert saved.id is not None
    store.record_send_attempt(
        event_type="custom.reminder",
        rule_id=saved.id,
        idempotency_key="old",
        recipient_count=1,
        status="failed",
        error="password=secret sent to director@example.org",
    )
    store.record_send_attempt(
        event_type="custom.reminder",
        rule_id=saved.id,
        idempotency_key="new",
        recipient_count=1,
        status="sent",
    )
    store.schedule_notification(
        event_type="custom.reminder",
        rule_id=saved.id,
        idempotency_key="scheduled",
        due_date=__import__("datetime").date(2026, 7, 10),
        payload={"person_name": "Jane"},
    )

    audit = store.list_audit(saved.id, limit=2)
    scheduled = store.list_scheduled_notifications(saved.id, status="pending", limit=5)

    assert [row["idempotency_key"] for row in audit] == ["new", "old"]
    assert audit[1]["error"] == "password=secret sent to director@example.org"
    assert scheduled[0]["event_type"] == "custom.reminder"
    assert scheduled[0]["payload"] == {"person_name": "Jane"}


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


def test_notification_service_resolves_role_recipients_by_payload_school(tmp_path: Path) -> None:
    rule = NotificationRule(
        event_type="staffing.assignment.need_now",
        label="Need now",
        subject_template="Need now",
        body_template="Body",
        recipients=[
            NotificationRecipient(email="", name="Hiring Manager", role_label="Hiring Manager", recipient_type="role", role_key="hiring_manager"),
            NotificationRecipient(email="", name="Director", role_label="Director", recipient_type="role", role_key="director"),
            NotificationRecipient(email="director@launchpadpreschoolHAW.com", role_label="Explicit duplicate"),
        ],
    )

    resolved, summary, error = resolve_notification_recipients(rule, {"school": "Hawthorne"})

    assert error == ""
    assert resolved == ["recruiting@launchpadpreschool.com", "director@launchpadpreschoolHAW.com"]
    assert summary == "hiring manager + school director"


def test_notification_service_resolves_candidate_and_executive_director_roles(tmp_path: Path) -> None:
    rule = NotificationRule(
        event_type="offer.accepted",
        label="Offer accepted",
        subject_template="Accepted",
        body_template="Body",
        recipients=[
            NotificationRecipient(recipient_type="role", role_key="candidate", role_label="Candidate"),
            NotificationRecipient(recipient_type="role", role_key="executive_director", role_label="Executive Director"),
        ],
    )

    resolved, summary, error = resolve_notification_recipients(
        rule,
        {"candidate_email": "candidate@example.org"},
    )

    assert error == ""
    assert resolved == ["candidate@example.org", "deidre@launchpadpreschool.com"]
    assert summary == "candidate + executive director"


def test_notification_service_blocks_missing_candidate_email_for_candidate_role(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Candidate offer",
            subject_template="Offer",
            body_template="Body",
            recipients=[NotificationRecipient(recipient_type="role", role_key="candidate", role_label="Candidate")],
        )
    )
    service = NotificationService(store=store, email_settings=_settings(), send_email=lambda *_: None)

    [result] = service.emit_event("offer.approved", {"candidate_name": "Jane"}, "offer-no-email")

    assert result.status == "blocked"
    assert result.error == "Candidate recipient requires a valid candidate email."
    [audit] = store.list_audit(saved.id, limit=1)
    assert audit["error"] == "Candidate recipient requires a valid candidate email."


def test_notification_service_blocks_unknown_school_for_director_role(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="staffing.assignment.need_now",
            label="Need now",
            subject_template="Need now: {position_name}",
            body_template="{school} needs {position_name}.",
            recipients=[NotificationRecipient(email="", name="Director", role_label="Director", recipient_type="role", role_key="director")],
        )
    )
    service = NotificationService(store=store, email_settings=_settings(), send_email=lambda *_: None)

    [result] = service.emit_event(
        "staffing.assignment.need_now",
        {"position_name": "Teacher 1", "school": "Unknown School"},
        "need-now-unknown-school",
    )

    assert result.status == "blocked"
    assert result.error == "Director recipient requires a supported school."
    [audit] = store.list_audit(saved.id, limit=1)
    assert audit["recipient_count"] == 0
    assert audit["error"] == "Director recipient requires a supported school."


def test_notification_service_attaches_offer_pdf_from_payload(tmp_path: Path) -> None:
    pdf_path = tmp_path / "offer.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Offer approved",
            subject_template="Offer for {candidate_name}",
            body_template="Attached.",
            recipients=[NotificationRecipient(recipient_type="role", role_key="candidate", role_label="Candidate")],
        )
    )
    sent: list[tuple[list[str], str, str, list[str]]] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda settings, recipients, subject, body, attachments: sent.append((recipients, subject, body, attachments)),
    )

    [result] = service.emit_event(
        "offer.approved",
        {"candidate_name": "Jane Doe", "candidate_email": "jane@example.org", "offer_pdf_path": str(pdf_path)},
        "offer-pdf",
    )

    assert result.status == "sent"
    assert sent == [(["jane@example.org"], "Offer for Jane Doe", "Attached.", [str(pdf_path)])]


def test_notification_service_test_send_uses_disabled_rule_without_enabling(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="staffing.assignment.need_now",
            label="Need now",
            subject_template="Need now: {position_name}",
            body_template="{school} needs {position_name}.",
            recipients=[NotificationRecipient(email="director@example.org")],
            active=False,
        )
    )
    sent: list[tuple[list[str], str, str]] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda settings, recipients, subject, body: sent.append((recipients, subject, body)),
    )

    result = service.send_test(
        saved.id or 0,
        {"position_name": "Teacher 1", "school": "Hawthorne"},
        "test-need-now",
    )

    [rule] = store.list_rules("staffing.assignment.need_now")
    assert result.status == "sent"
    assert rule.active is False
    assert sent == [(["director@example.org"], "Need now: Teacher 1", "Hawthorne needs Teacher 1.")]
    [audit] = store.list_audit(saved.id, limit=1)
    assert audit["status"] == "sent"
    assert audit["event_type"] == "staffing.assignment.need_now.test"


def test_notification_service_test_send_blocks_missing_smtp_and_sanitizes_audit(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="custom.reminder",
            label="Reminder",
            subject_template="Reminder for {person_name}",
            body_template="Body",
            recipients=[NotificationRecipient(email="director@example.org")],
            active=False,
        )
    )
    service = NotificationService(store=store, email_settings=EmailSettings())

    result = service.send_test(saved.id or 0, {"person_name": "Jane"}, "test-missing-smtp")

    assert result.status == "blocked"
    assert result.error == "SMTP settings are incomplete."
    [audit] = store.list_audit(saved.id, limit=1)
    assert audit["status"] == "blocked"
    assert "@" not in audit["error"]
    assert "password" not in audit["error"].casefold()


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
    with pytest.raises(ValueError, match="Unknown template variables: missing"):
        store.save_rule(
            NotificationRule(
                event_type="offer.accepted",
                label="Broken template",
                subject_template="Accepted: {missing}",
                body_template="Body",
                recipients=[NotificationRecipient(email="director@example.org")],
            )
        )


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
