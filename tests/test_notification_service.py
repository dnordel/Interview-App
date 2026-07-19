from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

from notification_models import NotificationCondition, NotificationRecipient, NotificationRule
import notification_service
from notification_service import (
    NotificationDirectory,
    NotificationService,
    StaffingNotificationScheduler,
    candidate_notification_test_recipient,
    missing_email_account_fields,
    notification_conditions_match,
    resolve_notification_recipients,
    verify_email_account_connections,
)
from notification_store import NotificationStore
from staffing_store import StaffingStore
from onboarding_operations import EmailSettings


def _settings() -> EmailSettings:
    return EmailSettings(
        sender_email="sender@example.org",
        account_type="IMAP",
        imap_or_pop_host="imap.example.org",
        smtp_host="smtp.example.org",
        smtp_username="sender@example.org",
        smtp_password="test-secret",
    )


def test_candidate_notification_test_recipient_defaults_to_safe_admin_address() -> None:
    rule = NotificationRule(
        event_type="offer.approved",
        label="Candidate offer",
        subject_template="Offer",
        body_template="Body",
        recipients=[NotificationRecipient(recipient_type="role", role_key="candidate")],
    )

    assert candidate_notification_test_recipient(rule) == "davidn@launchpadpreschool.com"


def test_candidate_notification_test_recipient_accepts_edited_safe_address() -> None:
    rule = NotificationRule(
        event_type="offer.approved",
        label="Candidate offer",
        subject_template="Offer",
        body_template="Body",
        recipients=[NotificationRecipient(recipient_type="role", role_key="candidate")],
    )

    assert candidate_notification_test_recipient(rule, " qa@example.org ") == "qa@example.org"


def test_candidate_notification_test_recipient_rejects_invalid_edited_address() -> None:
    rule = NotificationRule(
        event_type="offer.approved",
        label="Candidate offer",
        subject_template="Offer",
        body_template="Body",
        recipients=[NotificationRecipient(recipient_type="role", role_key="candidate")],
    )

    with pytest.raises(ValueError, match="valid candidate test recipient"):
        candidate_notification_test_recipient(rule, "not-an-email")


def test_candidate_notification_test_recipient_skips_non_candidate_rule() -> None:
    rule = NotificationRule(
        event_type="staffing.assignment.need_now",
        label="Director notice",
        subject_template="Need now",
        body_template="Body",
        recipients=[NotificationRecipient(recipient_type="role", role_key="director")],
    )

    assert candidate_notification_test_recipient(rule) == ""


def test_shared_email_completeness_names_every_missing_operator_field() -> None:
    missing = missing_email_account_fields(notification_service.EmailSettings(account_type=""))

    assert missing == (
        "email address",
        "incoming account type",
        "incoming server",
        "SMTP server",
        "username",
        "password",
    )


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows DPAPI only")
def test_email_settings_dpapi_round_trip_and_session_only_mode(tmp_path: Path) -> None:
    path = tmp_path / "hiring-manager-email.json"
    remembered = notification_service.EmailSettings(
        sender_email="davidn@launchpadpreschool.com",
        smtp_host="smtp.example.org",
        username="davidn@launchpadpreschool.com",
        password="top-secret",
        smtp_password="top-secret",
        remember_password=True,
    )

    notification_service.save_email_account_settings(remembered, path)

    raw = path.read_text(encoding="utf-8")
    loaded = notification_service.load_email_account_settings(path)
    assert "top-secret" not in raw
    assert "dpapi:" in raw
    assert loaded.password == "top-secret"

    session_only = replace(remembered, remember_password=False)
    notification_service.save_email_account_settings(session_only, path)
    payload = json.loads(path.read_text(encoding="utf-8"))["email"]
    assert payload["password"] == ""
    assert payload["smtp_password"] == ""


def test_shared_config_password_round_trip_does_not_depend_on_dpapi_or_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "email-account.json"
    settings = notification_service.EmailSettings(
        sender_email="shared@example.org",
        smtp_host="smtp.example.org",
        username="shared@example.org",
        password="portable-secret",
        smtp_username="shared@example.org",
        smtp_password="portable-secret",
        password_storage="shared_config",
    )
    monkeypatch.setattr(notification_service, "protect_secret", lambda _value: pytest.fail("DPAPI used"))

    notification_service.save_email_account_settings(settings, path)
    notification_service._SESSION_EMAIL_PASSWORDS.clear()
    loaded = notification_service.load_email_account_settings(path)

    assert loaded.password_storage == "shared_config"
    assert loaded.smtp_password == "portable-secret"


def test_verify_email_account_authenticates_imap_then_smtp_starttls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeImap:
        def __init__(self, host: str, port: int, *, ssl_context: object, timeout: int) -> None:
            calls.append(("imap-connect", (host, port, bool(ssl_context), timeout)))

        def login(self, username: str, password: str) -> None:
            calls.append(("imap-login", (username, password)))

        def logout(self) -> None:
            calls.append(("imap-logout", None))

    class FakeSmtp:
        def __init__(self, host: str, port: int, *, timeout: int) -> None:
            calls.append(("smtp-connect", (host, port, timeout)))

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def starttls(self, *, context: object) -> None:
            calls.append(("smtp-starttls", bool(context)))

        def login(self, username: str, password: str) -> None:
            calls.append(("smtp-login", (username, password)))

    monkeypatch.setattr(notification_service.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(notification_service.smtplib, "SMTP", FakeSmtp)
    settings = notification_service.EmailSettings(
        account_type="IMAP",
        sender_email="shared@example.org",
        imap_or_pop_host="imap.example.org",
        imap_or_pop_port=993,
        incoming_encryption="SSL/TLS",
        smtp_host="smtp.example.org",
        smtp_port=587,
        smtp_encryption="STARTTLS",
        smtp_username="shared@example.org",
        smtp_password="portable-secret",
    )

    verify_email_account_connections(settings)

    assert calls == [
        ("imap-connect", ("imap.example.org", 993, True, 30)),
        ("imap-login", ("shared@example.org", "portable-secret")),
        ("imap-logout", None),
        ("smtp-connect", ("smtp.example.org", 587, 30)),
        ("smtp-starttls", True),
        ("smtp-login", ("shared@example.org", "portable-secret")),
    ]


def test_verify_email_account_supports_pop3_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakePop:
        def __init__(self, host: str, port: int, *, timeout: int, context: object) -> None:
            calls.append(("pop-connect", (host, port, timeout, bool(context))))

        def user(self, username: str) -> None:
            calls.append(("pop-user", username))

        def pass_(self, password: str) -> None:
            calls.append(("pop-password", password))

        def quit(self) -> None:
            calls.append(("pop-quit", None))

    class FakeSmtp:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def login(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(notification_service.poplib, "POP3_SSL", FakePop)
    monkeypatch.setattr(notification_service.smtplib, "SMTP", FakeSmtp)
    settings = notification_service.EmailSettings(
        account_type="POP3",
        sender_email="shared@example.org",
        imap_or_pop_host="pop.example.org",
        imap_or_pop_port=995,
        incoming_encryption="SSL/TLS",
        smtp_host="smtp.example.org",
        smtp_encryption="None",
        smtp_username="shared@example.org",
        smtp_password="portable-secret",
    )

    verify_email_account_connections(settings)

    assert calls == [
        ("pop-connect", ("pop.example.org", 995, 30, True)),
        ("pop-user", "shared@example.org"),
        ("pop-password", "portable-secret"),
        ("pop-quit", None),
    ]


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

        def login(self, _username: str, _password: str) -> None:
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
    assert sent == [(["tester@example.org"], "[THIS IS A TEST] Hello Alex", "Hi Alex")]
    assert store.list_audit() == []


def test_candidate_notification_preview_uses_shared_default_without_explicit_recipient(tmp_path: Path) -> None:
    sent: list[list[str]] = []
    service = NotificationService(
        store=NotificationStore(tmp_path / "notifications.sqlite3"),
        email_settings=_settings(),
        send_email=lambda _settings, recipients, _subject, _body: sent.append(recipients),
    )
    rule = NotificationRule(
        event_type="custom.candidate",
        label="Candidate notice",
        subject_template="Test",
        body_template="Body",
        recipients=[NotificationRecipient(recipient_type="role", role_key="candidate")],
        active=False,
    )

    result = service.send_test_preview(rule, {}, "", "candidate-preview-default")

    assert result.status == "sent"
    assert sent == [["davidn@launchpadpreschool.com"]]


def test_notification_service_preview_blocks_incomplete_shared_email_before_delivery(tmp_path: Path) -> None:
    delivered: list[str] = []
    service = NotificationService(
        store=NotificationStore(tmp_path / "notifications.sqlite3"),
        email_settings=notification_service.EmailSettings(account_type=""),
        send_email=lambda *_args: delivered.append("sent"),
    )
    rule = NotificationRule(
        event_type="custom.preview",
        label="Preview",
        subject_template="Hello",
        body_template="Body",
        active=False,
    )

    result = service.send_test_preview(rule, {}, "tester@example.org", "missing-shared-email")

    assert result.status == "blocked"
    assert result.error == "Shared email account settings are incomplete."
    assert delivered == []


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
    assert "Offer - Launch Pad Learning {school}" == events["offer.approved"].subject_template
    assert [(recipient.recipient_type, recipient.role_key) for recipient in events["offer.approved"].recipients] == [
        ("role", "candidate"),
        ("role", "executive_director"),
    ]
    assert [(recipient.recipient_type, recipient.role_key) for recipient in events["offer.accepted"].recipients] == [
        ("role", "candidate"),
        ("role", "director"),
        ("role", "office_manager"),
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


def test_notification_store_seeds_ten_staffing_workflow_system_rules(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    store.ensure_default_rules()

    rules = {rule.event_type: rule for rule in store.list_rules()}
    expected = {
        "interview.rating.qualified",
        "director.interview.hire",
        "offer.approved",
        "offer.accepted",
        "employment.start.today",
        "permit.eligible.50d",
        "permit.escalation.90d",
        "employment.notice.given",
        "employment.last_day",
        "staffing.assignment.need_now",
    }
    assert expected <= set(rules)
    assert all(rules[event].system_rule for event in expected)
    assert [item.role_key for item in rules["director.interview.hire"].recipients] == [
        "hr_manager",
        "executive_director",
    ]
    assert [item.role_key for item in rules["employment.notice.given"].recipients] == [
        "director",
        "office_manager",
        "executive_director",
        "payroll",
        "hr_manager",
    ]
    assert rules["offer.approved"].sender_account == "hiring_manager"
    assert rules["offer.approved"].required_attachment_key == "offer_pdf_path"
    assert rules["offer.accepted"].required_attachment_key == "onboarding_guide_path"
    permit_50 = rules["permit.eligible.50d"]
    assert (permit_50.trigger_timing, permit_50.date_field, permit_50.offset_days) == (
        "date_offset",
        "start_date",
        50,
    )
    assert [(item.field, item.operator, item.value) for item in permit_50.conditions] == [
        ("permit_status", "in", "unknown, no_permit_or_application"),
        ("position_type", "in", "teacher, aide, assistant director"),
    ]
    permit_90 = rules["permit.escalation.90d"]
    assert (permit_90.trigger_timing, permit_90.date_field, permit_90.offset_days) == (
        "date_offset",
        "start_date",
        90,
    )
    assert [(item.field, item.operator, item.value) for item in permit_90.conditions] == [
        ("permit_status", "in", "unknown, no_permit_or_application"),
        ("position_type", "in", "teacher, aide, assistant director"),
    ]


def test_existing_permit_system_rule_gains_logic_without_losing_user_disable(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="permit.escalation.90d",
            label="System: permit escalation",
            subject_template="{candidate_name}'s Permit Status",
            body_template="Body for {candidate_name}",
            recipients=[NotificationRecipient(recipient_type="role", role_key="director")],
            active=False,
            system_rule=True,
            user_disabled=True,
        )
    )

    store.ensure_default_rules()

    [upgraded] = store.list_rules("permit.escalation.90d")
    assert upgraded.user_disabled is True
    assert upgraded.active is False
    assert (upgraded.trigger_timing, upgraded.date_field, upgraded.offset_days) == (
        "date_offset",
        "start_date",
        90,
    )
    assert [(item.field, item.operator, item.value) for item in upgraded.conditions] == [
        ("permit_status", "in", "unknown, no_permit_or_application"),
        ("position_type", "in", "teacher, aide, assistant director"),
    ]


def test_system_rules_activate_when_dependencies_are_ready_and_preserve_user_disable(tmp_path: Path) -> None:
    guide = tmp_path / "guide.pdf"
    guide.write_bytes(b"%PDF-1.4\n")
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.ensure_default_rules()
    directory = replace(NotificationDirectory.defaults(), onboarding_guide_path=str(guide))
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        hiring_manager_email_settings=_settings(),
        directory=directory,
    )

    assert service.activate_ready_system_rules() == 10
    need_now = store.list_rules("staffing.assignment.need_now")[0]
    assert need_now.active is True

    store.set_rule_active(need_now.id or 0, False)
    service.activate_ready_system_rules()
    assert store.get_rule(need_now.id or 0).active is False
    assert store.get_rule(need_now.id or 0).user_disabled is True


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
    assert [recipient.role_key for recipient in backfilled.recipients] == ["hr_manager"]
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


def test_notification_service_retries_failed_send_but_permanently_dedupes_success(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="employment.start.today",
            label="Start",
            subject_template="Start",
            body_template="Body",
            recipients=[NotificationRecipient(email="hr@example.org")],
        )
    )
    attempts = 0

    def send(*_args: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary outage")

    service = NotificationService(store=store, email_settings=_settings(), send_email=send)

    assert service.emit_event("employment.start.today", {}, "employee:1:start")[0].status == "failed"
    assert service.emit_event("employment.start.today", {}, "employee:1:start")[0].status == "sent"
    assert service.emit_event("employment.start.today", {}, "employee:1:start")[0].status == "duplicate"
    assert attempts == 2


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


def test_notification_directory_resolves_editable_hr_payroll_and_school_office_manager() -> None:
    directory = NotificationDirectory.defaults()
    assert directory.hiring_manager == "recruiting@launchpadpreschool.com"
    assert directory.hr_manager == "recruiting@launchpadpreschool.com"
    assert directory.director_names == {
        "hawthorne": "Netsi",
        "palmdale": "Edith",
        "north long beach": "Claudia",
        "long beach": "Claudia",
    }
    rule = NotificationRule(
        event_type="employment.notice.given",
        label="Notice",
        subject_template="Notice",
        body_template="Body",
        recipients=[
            NotificationRecipient(recipient_type="role", role_key="hr_manager"),
            NotificationRecipient(recipient_type="role", role_key="payroll"),
            NotificationRecipient(recipient_type="role", role_key="office_manager"),
        ],
    )

    resolved, _, error = resolve_notification_recipients(
        rule, {"school": "North Long Beach"}, directory=directory
    )

    assert error == ""
    assert resolved == [
        "recruiting@launchpadpreschool.com",
        "payroll@launchpadpreschool.com",
        "admin-nlb@launchpadpreschool.com",
    ]


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


def test_candidate_offer_preview_uses_active_notification_settings_template(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.approved",
            label="Configured candidate offer",
            subject_template="Configured offer for {candidate_name}",
            body_template="Hello {honorific} {candidate_name} at {school_location}.",
            recipients=[NotificationRecipient(recipient_type="role", role_key="candidate")],
            active=True,
        )
    )
    service = NotificationService(store=store, email_settings=_settings(), send_email=lambda *_: None)

    preview = service.render_candidate_event_preview(
        "offer.approved",
        {
            "candidate_name": "Jane Doe",
            "honorific": "Ms.",
            "school_location": "Palmdale",
        },
    )

    assert preview == "Subject: Configured offer for Jane Doe\n\nHello Ms. Jane Doe at Palmdale."


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
    assert sent == [
        (["director@example.org"], "[THIS IS A TEST] Need now: Teacher 1", "Hawthorne needs Teacher 1.")
    ]
    [audit] = store.list_audit(saved.id, limit=1)
    assert audit["status"] == "sent"
    assert audit["event_type"] == "staffing.assignment.need_now.test"


def test_saved_candidate_notification_test_uses_sole_safe_default_recipient(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="custom.candidate",
            label="Candidate offer",
            subject_template="Offer for {candidate_name}",
            body_template="Offer body",
            recipients=[
                NotificationRecipient(recipient_type="role", role_key="candidate"),
                NotificationRecipient(email="director@example.org"),
            ],
            active=False,
        )
    )
    sent: list[tuple[list[str], str]] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda _settings, recipients, subject, _body: sent.append((recipients, subject)),
    )

    result = service.send_test(
        saved.id or 0,
        {"candidate_name": "Real Candidate", "candidate_email": "real-candidate@example.org"},
        "candidate-safe-test",
    )

    assert result.status == "sent"
    assert sent == [
        (["davidn@launchpadpreschool.com"], "[THIS IS A TEST] Offer for Real Candidate")
    ]


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


def test_notification_rule_conditions_use_persisted_db_payload_values(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    saved = store.save_rule(
        NotificationRule(
            event_type="permit.escalation.90d",
            label="Missing permit at 90 days",
            subject_template="Permit: {candidate_name}",
            body_template="Permit status: {permit_status}",
            recipients=[NotificationRecipient(email="director@example.org")],
            conditions=[
                NotificationCondition(
                    field="permit_status",
                    operator="in",
                    value="unknown, no_permit_or_application",
                )
            ],
        )
    )
    sent: list[str] = []
    service = NotificationService(
        store=store,
        email_settings=_settings(),
        send_email=lambda _settings, _recipients, subject, _body: sent.append(subject),
    )

    approved = service.emit_event(
        "permit.escalation.90d",
        {"candidate_name": "Jane Doe", "permit_status": "teacher_permit_approved"},
        "permit-approved",
    )
    missing = service.emit_event(
        "permit.escalation.90d",
        {"candidate_name": "Alex Kim", "permit_status": "unknown"},
        "permit-missing",
    )

    assert store.get_rule(saved.id or 0).conditions == saved.conditions
    assert approved[0].status == "condition_not_met"
    assert missing[0].status == "sent"
    assert sent == ["Permit: Alex Kim"]


@pytest.mark.parametrize(
    ("condition", "payload", "expected"),
    [
        (NotificationCondition("school", "equals", "Palmdale"), {"school": "palmdale"}, True),
        (NotificationCondition("school", "not_equals", "Hawthorne"), {"school": "Palmdale"}, True),
        (NotificationCondition("position_type", "not_in", "Chef, Office"), {"position_type": "Teacher"}, True),
        (NotificationCondition("position", "contains", "Teacher"), {"position": "Preschool Teacher"}, True),
        (NotificationCondition("position", "not_contains", "Director"), {"position": "Teacher"}, True),
        (NotificationCondition("candidate_email", "is_blank"), {"candidate_email": ""}, True),
        (NotificationCondition("candidate_email", "is_not_blank"), {"candidate_email": "a@example.org"}, True),
        (NotificationCondition("interview_score", "greater_than", "65"), {"interview_score": "80"}, True),
        (NotificationCondition("weekly_hours", "greater_than_or_equal", "30"), {"weekly_hours": "30"}, True),
        (NotificationCondition("ece_units", "less_than", "12"), {"ece_units": "6"}, True),
        (NotificationCondition("director_interview_score", "less_than_or_equal", "7"), {"director_interview_score": "8"}, False),
    ],
)
def test_notification_conditions_support_curated_safe_operators(condition, payload, expected) -> None:
    assert notification_conditions_match([condition], payload) is expected


@pytest.mark.parametrize(
    "condition",
    [
        NotificationCondition("DROP TABLE people", "equals", "x"),
        NotificationCondition("permit_status", "run_python", "x"),
    ],
)
def test_notification_store_rejects_unsafe_condition_schema(tmp_path: Path, condition) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    with pytest.raises(ValueError, match="condition"):
        store.save_rule(
            NotificationRule(
                event_type="permit.escalation.90d",
                label="Unsafe",
                subject_template="Permit",
                body_template="Body",
                recipients=[NotificationRecipient(email="director@example.org")],
                conditions=[condition],
            )
        )


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


def test_staffing_scheduler_uses_exact_day_noon_and_permit_role_status_rules(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    today_id = store.create_assignment(
        school="Hawthorne", classroom="Harmony", position_name="Teacher 1", position_type="Teacher",
        status="filled", person_name="Today Teacher", start_date="2026-07-16", now="2026-07-16T08:00:00Z",
    )
    eligible_id = store.create_assignment(
        school="Palmdale", classroom="Destiny", position_name="Aide 1", position_type="Aide",
        status="filled", person_name="Permit Aide", start_date="2026-05-01", now="2026-07-16T08:00:00Z",
    )
    escalation_id = store.create_assignment(
        school="North Long Beach", classroom="Office", position_name="Assistant Director", position_type="Assistant Director",
        status="filled", person_name="Assistant Director", start_date="2026-04-01", now="2026-07-16T08:00:00Z",
    )
    with store.connect() as conn:
        conn.execute("UPDATE people SET final_working_day = '2026-07-16' WHERE id = (SELECT person_id FROM assignments WHERE id = ?)", (today_id,))
        conn.commit()
    events: list[tuple[str, dict[str, str]]] = []
    emitter = SimpleNamespace(emit_event=lambda event, payload, key: events.append((event, payload)) or [])

    StaffingNotificationScheduler(
        staffing_store=store,
        notification_service=emitter,
        now=lambda: datetime(2026, 7, 16, 11, 59),
        rollout_date=datetime(2026, 1, 1).date(),
        candidate_contact_resolver=lambda name, school: {
            "email": "assistant@example.org", "honorific": "Ms."
        },
    ).run()
    event_names = [event for event, _payload in events]
    assert "employment.start.today" in event_names
    assert "employment.last_day" not in event_names
    assert "permit.eligible.50d" in event_names
    assert "permit.escalation.90d" in event_names
    escalation_payload = next(payload for event, payload in events if event == "permit.escalation.90d")
    assert escalation_payload["candidate_email"] == "assistant@example.org"

    events.clear()
    StaffingNotificationScheduler(
        staffing_store=store,
        notification_service=emitter,
        now=lambda: datetime(2026, 7, 16, 12, 0),
        rollout_date=datetime(2026, 1, 1).date(),
    ).run()
    assert "employment.last_day" in [event for event, _payload in events]
    assert eligible_id and escalation_id


def test_notification_rollout_date_is_created_once_and_persisted(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")

    assert store.get_or_create_rollout_date(date(2026, 7, 16)) == date(2026, 7, 16)
    assert store.get_or_create_rollout_date(date(2026, 8, 1)) == date(2026, 7, 16)


def test_system_defaults_are_added_without_overwriting_existing_custom_rule(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.sqlite3")
    store.save_rule(
        NotificationRule(
            event_type="offer.accepted",
            label="My custom accepted rule",
            subject_template="Accepted",
            body_template="Custom body",
            recipients=[NotificationRecipient(email="custom@example.org")],
            active=False,
        )
    )

    store.ensure_default_rules()

    rules = store.list_rules("offer.accepted")
    assert len(rules) == 2
    assert any(rule.label == "My custom accepted rule" and not rule.system_rule for rule in rules)
    assert any(rule.system_rule and rule.required_attachment_key == "onboarding_guide_path" for rule in rules)


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
