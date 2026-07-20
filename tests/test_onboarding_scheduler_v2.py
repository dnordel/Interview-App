from datetime import datetime, timezone

import pytest

from onboarding_scheduler_v2 import OnboardingAutomaticReminderScheduler
from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import OnboardingStore


def _service(tmp_path, *, role="admin"):
    access = OnboardingAccess(
        role=role,
        actor=f"{role}-1",
        school_scope="Palmdale" if role == "director" else "",
    )
    service = OnboardingService(OnboardingStore(tmp_path / f"{role}.sqlite3"), access)
    employee = service.create_employee(
        legal_name="Scheduler Test",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2026-07-01",
        start_date="2026-07-15",
    )
    service.create_task(
        employee_id=employee.id,
        title="Due setup",
        owner_role="Director",
        due_date="2026-07-20",
    )
    return service


def test_admin_scheduler_runs_once_on_weekday_eight_am_and_persists_health(tmp_path):
    service = _service(tmp_path)
    sent = []
    scheduler = OnboardingAutomaticReminderScheduler(
        service,
        recipient_resolver=lambda _school, _role: "director@example.com",
        admin_fallback_email="admin@example.com",
        sender=sent.append,
    )
    now = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)

    first = scheduler.run_if_due(now)
    duplicate = scheduler.run_if_due(now)

    assert first is not None and first.sent_count == 1
    assert duplicate is None
    assert len(sent) == 1
    health = service.scheduler_health()
    assert health["local_day"] == "2026-07-20"
    assert health["state"] == "success"
    assert health["sent_count"] == 1


def test_scheduler_skips_weekends_and_rejects_director_automation(tmp_path):
    admin = _service(tmp_path, role="admin")
    scheduler = OnboardingAutomaticReminderScheduler(
        admin,
        recipient_resolver=lambda _school, _role: "director@example.com",
        admin_fallback_email="admin@example.com",
        sender=lambda _message: None,
    )
    assert scheduler.run_if_due(datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)) is None

    director = _service(tmp_path, role="director")
    with pytest.raises(PermissionError, match="Admin"):
        OnboardingAutomaticReminderScheduler(
            director,
            recipient_resolver=lambda _school, _role: "director@example.com",
            admin_fallback_email="admin@example.com",
            sender=lambda _message: None,
        )


def test_scheduler_resolves_admin_fallback_fresh_for_each_run(tmp_path):
    service = _service(tmp_path)
    fallback = {"email": "first@example.com"}
    sent = []
    scheduler = OnboardingAutomaticReminderScheduler(
        service,
        recipient_resolver=lambda _school, _role: "",
        admin_fallback_email=lambda: fallback["email"],
        sender=sent.append,
    )
    fallback["email"] = "fresh@example.com"

    scheduler.run_if_due(datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc))

    assert [message.recipient for message in sent] == ["fresh@example.com"]
