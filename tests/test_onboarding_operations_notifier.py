from unittest.mock import patch

from onboarding_operations import EmailSettings
import onboarding_operations
from onboarding_operations import send_escalation_email, send_reminder_email, verify_email_connection
from onboarding_operations import ReminderItem


def test_send_reminder_email_uses_tls_context_when_enabled():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        reminder_recipients="recipient@example.com",
        use_tls=True,
    )
    reminders = [
        ReminderItem(
            employee_id="e1",
            employee_name="Pat",
            task_id="t1",
            title="Submit docs",
            due_date="2026-01-01",
        )
    ]

    with patch("onboarding_operations.ssl.create_default_context") as mock_create_context:
        tls_context = object()
        mock_create_context.return_value = tls_context

        with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
            send_reminder_email(settings, reminders)

    server = mock_smtp.return_value.__enter__.return_value
    server.starttls.assert_called_once_with(context=tls_context)


def test_send_reminder_email_uses_smtp_ssl_for_ssl_tls_encryption():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        reminder_recipients="recipient@example.com",
        smtp_encryption="SSL/TLS",
    )
    reminders = [
        ReminderItem(
            employee_id="e1",
            employee_name="Pat",
            task_id="t1",
            title="Submit docs",
            due_date="2026-01-01",
        )
    ]

    with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
        with patch("onboarding_operations.smtplib.SMTP_SSL") as mock_smtp_ssl:
            send_reminder_email(settings, reminders)

    mock_smtp.assert_not_called()
    mock_smtp_ssl.assert_called_once_with("smtp.example.com", 465, timeout=30)


def test_send_reminder_email_skips_starttls_when_encryption_none():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=25,
        reminder_recipients="recipient@example.com",
        use_tls=True,
        smtp_encryption="None",
    )
    reminders = [
        ReminderItem(
            employee_id="e1",
            employee_name="Pat",
            task_id="t1",
            title="Submit docs",
            due_date="2026-01-01",
        )
    ]

    with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
        send_reminder_email(settings, reminders)

    server = mock_smtp.return_value.__enter__.return_value
    server.starttls.assert_not_called()


def test_email_settings_from_dict_maps_legacy_use_tls_false_to_no_encryption():
    settings = EmailSettings.from_dict({"sender_email": "sender@example.com", "use_tls": False})

    assert settings.smtp_encryption == "None"
    assert settings.use_tls is False


def test_verify_email_connection_uses_starttls_and_login_without_sending():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_encryption="STARTTLS",
    )

    with patch("onboarding_operations.ssl.create_default_context") as mock_create_context:
        tls_context = object()
        mock_create_context.return_value = tls_context
        with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
            verify_email_connection(settings)

    server = mock_smtp.return_value.__enter__.return_value
    server.starttls.assert_called_once_with(context=tls_context)
    server.login.assert_called_once_with("sender@example.com", "app-password")
    server.send_message.assert_not_called()


def test_verify_email_connection_uses_smtp_ssl_without_starttls():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_encryption="SSL/TLS",
    )

    with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
        with patch("onboarding_operations.smtplib.SMTP_SSL") as mock_smtp_ssl:
            verify_email_connection(settings)

    mock_smtp.assert_not_called()
    mock_smtp_ssl.assert_called_once_with("smtp.example.com", 465, timeout=30)
    server = mock_smtp_ssl.return_value.__enter__.return_value
    server.starttls.assert_not_called()
    server.login.assert_called_once_with("sender@example.com", "app-password")
    server.send_message.assert_not_called()


def test_send_reminder_email_renders_templates_when_present():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        reminder_recipients="recipient@example.com",
        reminder_subject_template="Reminder for {employee_summary}",
        reminder_body_template="Count: {count}\nSchool: {school}",
    )
    reminders = [
        ReminderItem(
            employee_id="e1",
            employee_name="Pat",
            task_id="t1",
            title="Submit docs",
            due_date="2026-01-01",
        )
    ]

    with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
        send_reminder_email(settings, reminders, school="Hawthorne")

    message = mock_smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert message["Subject"] == "Reminder for Pat"
    assert "School: Hawthorne" in message.get_content()


def test_send_escalation_email_falls_back_to_defaults_when_templates_blank():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        director_and_owners="director@example.com",
    )
    body_lines = ["Escalation line 1", "Escalation line 2"]

    with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
        send_escalation_email(settings, body_lines)

    message = mock_smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert message["Subject"] == "Escalation: Incomplete permit or LiveScan tasks"
    assert "Escalation line 1" in message.get_content()


def test_send_reminder_email_sanitizes_subject_header_injection_patterns():
    settings = EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        reminder_recipients="recipient@example.com",
        reminder_subject_template="Reminder\r\nBcc: bad@example.com {employee_summary}",
    )
    reminders = [
        ReminderItem(
            employee_id="e1",
            employee_name="Pat",
            task_id="t1",
            title="Submit docs",
            due_date="2026-01-01",
        )
    ]

    with patch("onboarding_operations.smtplib.SMTP") as mock_smtp:
        send_reminder_email(settings, reminders)

    message = mock_smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "\n" not in str(message["Subject"])
    assert "\r" not in str(message["Subject"])
    assert str(message["Subject"]) == "Reminder  Bcc: bad@example.com Pat"


def test_notifier_wrapper_exports_new_owner_helpers():
    assert send_reminder_email is onboarding_operations.send_reminder_email
