from datetime import datetime, timezone

import pytest

from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import OnboardingStore


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


def _director_service(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "reminders.sqlite3"),
        OnboardingAccess(role="director", actor="director-pmd", school_scope="Palmdale"),
    )
    employee = service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    due = service.create_task(
        employee_id=employee.id,
        title="Due task",
        owner_role="Director",
        watcher_roles=["Payroll"],
        due_date="2026-07-20",
    )
    dependency = service.create_task(
        employee_id=employee.id,
        title="Dependency",
        owner_role="IT",
        due_date="2026-07-20",
    )
    blocked = service.create_task(
        employee_id=employee.id,
        title="Blocked task",
        owner_role="Director",
        due_date="2026-07-19",
        dependency_ids=[dependency.id],
    )
    return service, employee, due, dependency, blocked


def test_preview_suppresses_blocked_tasks_and_uses_admin_fallback(tmp_path):
    service, _employee, due, dependency, blocked = _director_service(tmp_path)
    recipients = {("Palmdale", "Payroll"): "payroll@example.com"}

    preview = service.preview_reminders(
        recipient_resolver=lambda school, role: recipients.get((school, role), ""),
        admin_fallback_email="admin@example.com",
        now=NOW,
    )

    assert preview.expires_at.isoformat() == "2026-07-20T08:10:00+00:00"
    assert {(message.role, message.recipient, message.task_ids) for message in preview.messages} == {
        ("Director", "admin@example.com", (due.id,)),
        ("Payroll", "payroll@example.com", (due.id,)),
        ("IT", "admin@example.com", (dependency.id,)),
    }
    assert any("Director" in warning and "fallback" in warning for warning in preview.warnings)
    assert all(blocked.id not in message.task_ids for message in preview.messages)


def test_preview_expires_invalidates_and_director_duplicate_batch_is_blocked(tmp_path):
    service, employee, _due, _dependency, _blocked = _director_service(tmp_path)
    preview = service.preview_reminders(
        recipient_resolver=lambda _school, _role: "role@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
    )
    with pytest.raises(ValueError, match="expired"):
        service.send_reminder_preview(
            preview.token,
            sender=lambda _message: None,
            now=datetime(2026, 7, 20, 8, 11, tzinfo=timezone.utc),
            confirmed=True,
        )

    fresh = service.preview_reminders(
        recipient_resolver=lambda _school, _role: "role@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
    )
    service.create_task(
        employee_id=employee.id,
        title="New task",
        owner_role="Director",
        due_date="2026-07-20",
    )
    with pytest.raises(ValueError, match="data changed"):
        service.send_reminder_preview(fresh.token, sender=lambda _message: None, now=NOW, confirmed=True)

    valid = service.preview_reminders(
        recipient_resolver=lambda _school, _role: "role@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
    )
    result = service.send_reminder_preview(valid.token, sender=lambda _message: None, now=NOW, confirmed=True)
    assert result.sent_count == len(valid.messages)
    duplicate = service.preview_reminders(
        recipient_resolver=lambda _school, _role: "role@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
    )
    with pytest.raises(ValueError, match="already sent"):
        service.send_reminder_preview(duplicate.token, sender=lambda _message: None, now=NOW, confirmed=True)


def test_partial_retry_sends_only_failed_messages(tmp_path):
    service, _employee, _due, _dependency, _blocked = _director_service(tmp_path)
    preview = service.preview_reminders(
        recipient_resolver=lambda _school, role: f"{role.casefold()}@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
    )
    attempts: list[str] = []

    def first_send(message):
        attempts.append(message.role)
        if message.role == "Payroll":
            raise RuntimeError("smtp unavailable")

    result = service.send_reminder_preview(preview.token, sender=first_send, now=NOW, confirmed=True)
    assert result.failed_count == 1

    retried: list[str] = []
    retry = service.retry_failed_reminders(
        result.run_id,
        sender=lambda message: retried.append(message.role),
        now=NOW,
        confirmed=True,
    )

    assert retried == ["Payroll"]
    assert retry.sent_count == 1
    assert retry.failed_count == 0
    with pytest.raises(ValueError, match="no failed"):
        service.retry_failed_reminders(result.run_id, sender=lambda _message: None, now=NOW, confirmed=True)


def test_run_history_is_persisted_and_school_scoped(tmp_path):
    director, _employee, _due, _dependency, _blocked = _director_service(tmp_path)
    preview = director.preview_reminders(
        recipient_resolver=lambda _school, role: f"{role.casefold()}@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
    )
    result = director.send_reminder_preview(
        preview.token,
        sender=lambda _message: None,
        now=NOW,
        confirmed=True,
    )

    history = director.list_reminder_run_history()

    assert {row["run_id"] for row in history} == {result.run_id}
    assert {row["school"] for row in history} == {"Palmdale"}
    assert all("recipient" not in row for row in history)


def test_admin_duplicate_send_requires_override_reason(tmp_path):
    service = OnboardingService(
        OnboardingStore(tmp_path / "admin-reminders.sqlite3"),
        OnboardingAccess(role="admin", actor="admin-1"),
    )
    employee = service.create_employee(
        legal_name="Admin Reminder", school="Palmdale", role="Teacher",
        acceptance_date="2026-07-01", start_date="2026-07-15",
    )
    service.create_task(
        employee_id=employee.id, title="Due", owner_role="Director", due_date="2026-07-20"
    )
    resolver = lambda _school, _role: "director@example.com"
    first = service.preview_reminders(
        recipient_resolver=resolver, admin_fallback_email="admin@example.com", now=NOW
    )
    service.send_reminder_preview(first.token, sender=lambda _message: None, now=NOW, confirmed=True)
    duplicate = service.preview_reminders(
        recipient_resolver=resolver, admin_fallback_email="admin@example.com", now=NOW
    )

    with pytest.raises(ValueError, match="override requires a reason"):
        service.send_reminder_preview(
            duplicate.token, sender=lambda _message: None, now=NOW, confirmed=True
        )
    result = service.send_reminder_preview(
        duplicate.token,
        sender=lambda _message: None,
        now=NOW,
        confirmed=True,
        admin_override_reason="SMTP delivery was verified incomplete",
    )
    assert result.sent_count == 1


def test_preview_invalidates_when_notification_configuration_changes(tmp_path):
    service, _employee, _due, _dependency, _blocked = _director_service(tmp_path)
    preview = service.preview_reminders(
        recipient_resolver=lambda _school, _role: "role@example.com",
        admin_fallback_email="admin@example.com",
        now=NOW,
        config_revision="directory-v1",
    )

    with pytest.raises(ValueError, match="configuration changed"):
        service.send_reminder_preview(
            preview.token,
            sender=lambda _message: None,
            now=NOW,
            confirmed=True,
            config_revision="directory-v2",
        )
