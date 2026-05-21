from datetime import date

from onboarding_models import EmailSettings, Employee, EmployeeTask, ReminderCadence, TaskTemplate
from onboarding_reminder_runner import OnboardingReminderRunner


def _build_employee() -> Employee:
    return Employee(
        id="e1",
        name="Pat",
        acceptance_date="2026-01-01",
        start_date="2026-01-01",
        tasks=[
            EmployeeTask(
                id="task_due",
                template_id="template_due",
                title="Submit permit",
                due_date="2026-01-01",
                completed=False,
            ),
            EmployeeTask(
                id="task_escalate",
                template_id="template_escalate",
                title="Escalation: LiveScan incomplete",
                due_date="2026-01-01",
                completed=False,
            ),
        ],
    )


def _build_templates() -> list[TaskTemplate]:
    return [
        TaskTemplate(
            id="template_due",
            title="Submit permit",
            reference="start_date",
            offset_days=0,
            cadence=ReminderCadence(mode="daily", interval_days=1),
        ),
        TaskTemplate(
            id="template_escalate",
            title="Escalation: LiveScan incomplete",
            reference="start_date",
            offset_days=0,
            cadence=ReminderCadence(mode="daily", interval_days=1),
        ),
    ]


def _settings() -> EmailSettings:
    return EmailSettings(
        sender_email="sender@example.com",
        smtp_host="smtp.example.com",
        reminder_recipients="coach@example.com",
        director_and_owners="director@example.com",
    )


def test_preview_does_not_send_or_mutate_state():
    employee = _build_employee()
    reminder_calls: list[object] = []
    escalation_calls: list[object] = []

    runner = OnboardingReminderRunner(
        employees=[employee],
        templates=_build_templates(),
        email_settings=_settings(),
        reminder_sender=lambda *_args: reminder_calls.append(_args),
        escalation_sender=lambda *_args: escalation_calls.append(_args),
    )

    result = runner.preview(now_date=date(2026, 1, 1))

    assert result.dry_run is True
    assert result.counts["due_reminders"] == 2
    assert len(result.outcomes) == 2
    assert reminder_calls == []
    assert escalation_calls == []
    assert employee.tasks[0].last_reminder_sent is None


def test_run_sends_and_marks_reminders_when_send_succeeds():
    employee = _build_employee()

    runner = OnboardingReminderRunner(
        employees=[employee],
        templates=_build_templates(),
        email_settings=_settings(),
        reminder_sender=lambda *_args: "Reminder sent",
        escalation_sender=lambda *_args: "Escalation sent",
    )

    result = runner.run(now_date=date(2026, 1, 1), dry_run=False)

    assert result.dry_run is False
    assert result.counts["successful_sends"] == 2
    assert all(task.last_reminder_sent == "2026-01-01" for task in employee.tasks)


def test_run_does_not_mark_when_reminder_send_fails():
    employee = _build_employee()

    def _fail_reminder(*_args):
        raise RuntimeError("SMTP unavailable")

    runner = OnboardingReminderRunner(
        employees=[employee],
        templates=_build_templates(),
        email_settings=_settings(),
        reminder_sender=_fail_reminder,
        escalation_sender=lambda *_args: "Escalation sent",
    )

    result = runner.run(now_date=date(2026, 1, 1), dry_run=False)

    reminder_outcome = [item for item in result.outcomes if item.phase == "reminder"][0]
    assert reminder_outcome.success is False
    assert employee.tasks[0].last_reminder_sent is None


def test_send_result_contains_recipient_task_breakdown_and_channels():
    employee = _build_employee()
    runner = OnboardingReminderRunner(
        employees=[employee],
        templates=_build_templates(),
        email_settings=_settings(),
        reminder_sender=lambda *_args: "Reminder sent",
        escalation_sender=lambda *_args: "Escalation sent",
    )

    result = runner.run(now_date=date(2026, 1, 1), dry_run=False)

    assert "Pat" in result.task_breakdown
    assert len(result.task_breakdown["Pat"]) == 2
    assert len(result.escalation_candidates) == 1
    assert result.channel_results["email"]["sent"] == 2
    assert result.channel_results["in_app"]["sent"] == 2
    assert result.run_id.startswith("reminder_run_")
    assert result.ran_at.startswith("2026-01-01T00:00:00")
    payload = result.to_dict()
    assert payload["tasks"][0]["employee_name"] == "Pat"
    assert "task_breakdown" in payload


def test_send_failure_exposes_error_summaries():
    employee = _build_employee()

    def _fail_reminder(*_args):
        raise RuntimeError("SMTP unavailable")

    runner = OnboardingReminderRunner(
        employees=[employee],
        templates=_build_templates(),
        email_settings=_settings(),
        reminder_sender=_fail_reminder,
        escalation_sender=lambda *_args: "Escalation sent",
    )

    result = runner.run(now_date=date(2026, 1, 1), dry_run=False)

    assert result.error_summaries == ["SMTP unavailable"]
