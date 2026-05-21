from datetime import date

from onboarding_models import Employee, EmployeeTask, ReminderCadence, TaskTemplate
from onboarding_scheduler import calculate_due_date, collect_due_reminders, seed_employee_tasks, task_should_remind


def test_due_reminder_daily_interval():
    template = TaskTemplate(
        id="t1",
        title="Task",
        reference="start_date",
        offset_days=0,
        cadence=ReminderCadence(mode="daily", interval_days=2),
    )
    task = EmployeeTask(
        id="x",
        template_id="t1",
        title="Task",
        due_date="2026-01-01",
        completed=False,
        last_reminder_sent="2026-01-02",
    )
    assert task_should_remind(task, template, date(2026, 1, 3), blocked=False) is False
    assert task_should_remind(task, template, date(2026, 1, 4), blocked=False) is True


def test_collect_due_reminders_skips_completed_and_blocked():
    templates = [
        TaskTemplate(
            id="permit_applied",
            title="Permit",
            reference="start_date",
            offset_days=0,
            cadence=ReminderCadence(mode="daily", interval_days=1),
        ),
        TaskTemplate(
            id="escalate",
            title="Escalation: email",
            reference="start_date",
            offset_days=0,
            cadence=ReminderCadence(mode="daily", interval_days=1),
            depends_on_incomplete=["permit_applied"],
        ),
    ]
    employee = Employee(
        id="e1",
        name="Pat",
        acceptance_date="2026-01-01",
        start_date="2026-01-01",
        tasks=[
            EmployeeTask(id="a", template_id="permit_applied", title="Permit", due_date="2026-01-01", completed=True),
            EmployeeTask(id="b", template_id="escalate", title="Escalation: email", due_date="2026-01-01", completed=False),
        ],
    )
    reminders = collect_due_reminders([employee], templates, date(2026, 1, 5))
    assert reminders == []


def test_calculate_due_date_monthly_reference_applies_positive_offset():
    employee = Employee(
        id="e1",
        name="Pat",
        acceptance_date="2026-01-01",
        start_date="2026-01-10",
    )
    template = TaskTemplate(
        id="monthly_plus",
        title="Monthly Task",
        reference="monthly",
        offset_days=5,
        cadence=ReminderCadence(mode="once", interval_days=0),
    )

    assert calculate_due_date(employee, template, date(2026, 2, 20)) == date(2026, 2, 6)


def test_calculate_due_date_monthly_reference_applies_negative_offset_from_start_month_anchor():
    employee = Employee(
        id="e1",
        name="Pat",
        acceptance_date="2026-01-01",
        start_date="2026-03-15",
    )
    template = TaskTemplate(
        id="monthly_minus",
        title="Monthly Task",
        reference="monthly",
        offset_days=-3,
        cadence=ReminderCadence(mode="once", interval_days=0),
    )

    assert calculate_due_date(employee, template, date(2026, 2, 20)) == date(2026, 2, 26)


def test_seed_employee_tasks_copies_template_critical_metadata():
    employee = Employee(
        id="e1",
        name="Pat",
        acceptance_date="2026-01-01",
        start_date="2026-01-10",
    )
    templates = [
        TaskTemplate(
            id="setup_email",
            title="Set up email",
            reference="start_date",
            offset_days=-1,
            cadence=ReminderCadence(mode="daily", interval_days=1),
            critical=True,
            deadline_label="Before day 1",
        )
    ]

    seed_employee_tasks(employee, templates)

    assert employee.tasks[0].critical is True
    assert employee.tasks[0].deadline_label == "Before day 1"


def test_calculate_due_date_specific_date_reference_uses_selected_date_anchor():
    employee = Employee(
        id="e1",
        name="Pat",
        acceptance_date="2026-01-01",
        start_date="2026-01-10",
    )
    template = TaskTemplate(
        id="specific_anchor",
        title="Specific Date Task",
        reference="date:2026-05-20",
        offset_days=2,
        cadence=ReminderCadence(mode="once", interval_days=1),
    )

    assert calculate_due_date(employee, template, date(2026, 2, 20)) == date(2026, 5, 22)
