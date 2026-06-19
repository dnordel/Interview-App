import onboarding_operations
from onboarding_operations import EmailSettings, ReminderCadence, TaskTemplate, resolve_smtp_password


def test_reminder_cadence_from_dict_invalid_interval_falls_back_to_default():
    assert EmailSettings is onboarding_operations.EmailSettings
    cadence = ReminderCadence.from_dict({"mode": "daily", "interval_days": "not-a-number"})

    assert cadence.mode == "daily"
    assert cadence.interval_days == 1


def test_reminder_cadence_from_dict_interval_is_clamped_to_minimum():
    cadence = ReminderCadence.from_dict({"mode": "daily", "interval_days": 0})

    assert cadence.interval_days == 1


def test_email_settings_from_dict_invalid_ports_fall_back_to_defaults():
    settings = EmailSettings.from_dict({"smtp_port": "oops", "imap_or_pop_port": object()})

    assert settings.smtp_port == 587
    assert settings.imap_or_pop_port == 993


def test_email_settings_from_dict_ports_are_clamped_to_minimum():
    settings = EmailSettings.from_dict({"smtp_port": -25, "imap_or_pop_port": 0})

    assert settings.smtp_port == 1
    assert settings.imap_or_pop_port == 1


def test_task_template_from_dict_reads_critical_metadata():
    template = TaskTemplate.from_dict(
        {
            "id": "setup_email",
            "title": "Set up employee email",
            "critical": True,
            "deadline_label": "Before day 1",
        }
    )

    assert template.critical is True
    assert template.deadline_label == "Before day 1"


def test_email_settings_template_fields_round_trip():
    source = {
        "reminder_subject_template": "Reminder for {employee_summary}",
        "reminder_body_template": "Tasks:\n{task_summary}",
        "escalation_subject_template": "Escalation for {school}",
        "escalation_body_template": "Please review:\n{task_summary}",
    }

    settings = EmailSettings.from_dict(source)

    assert settings.to_dict()["reminder_subject_template"] == source["reminder_subject_template"]
    assert settings.to_dict()["reminder_body_template"] == source["reminder_body_template"]
    assert settings.to_dict()["escalation_subject_template"] == source["escalation_subject_template"]
    assert settings.to_dict()["escalation_body_template"] == source["escalation_body_template"]


def test_resolve_smtp_password_prefers_environment_override(monkeypatch):
    monkeypatch.setenv("ONBOARDING_SMTP_PASSWORD", "env-secret")

    assert resolve_smtp_password("file-secret") == "env-secret"
