from datetime import date

from onboarding_models import Employee, EmployeeTask
from onboarding_ui_helpers import build_onboarding_overview, sorted_tasks_for_display, task_status


def test_task_status_orders_urgency_levels():
    today = date(2026, 1, 10)
    assert task_status(EmployeeTask(id="a", template_id="", title="", due_date="2026-01-09"), today) == "overdue"
    assert task_status(EmployeeTask(id="b", template_id="", title="", due_date="2026-01-10"), today) == "due_today"
    assert task_status(EmployeeTask(id="c", template_id="", title="", due_date="2026-01-12"), today) == "due_soon"
    assert task_status(EmployeeTask(id="d", template_id="", title="", due_date="2026-01-20"), today) == "upcoming"
    assert task_status(EmployeeTask(id="e", template_id="", title="", due_date=None), today) == "unscheduled"
    done_task = EmployeeTask(id="f", template_id="", title="", due_date="2026-01-01", completed=True)
    assert task_status(done_task, today) == "completed"


def test_sorted_tasks_puts_most_urgent_first():
    today = date(2026, 1, 10)
    tasks = [
        EmployeeTask(id="3", template_id="", title="Upcoming", due_date="2026-01-20"),
        EmployeeTask(id="2", template_id="", title="DueToday", due_date="2026-01-10"),
        EmployeeTask(id="1", template_id="", title="Overdue", due_date="2026-01-09"),
    ]
    ordered = sorted_tasks_for_display(tasks, today)
    assert [task.id for task in ordered] == ["1", "2", "3"]


def test_build_onboarding_overview_rolls_up_employee_counts():
    today = date(2026, 1, 10)
    employee = Employee(
        id="emp1",
        name="Taylor",
        acceptance_date="2026-01-01",
        start_date="2026-01-10",
        tasks=[
            EmployeeTask(id="t1", template_id="", title="Email", due_date="2026-01-09"),
            EmployeeTask(id="t2", template_id="", title="Badge", due_date="2026-01-10"),
            EmployeeTask(id="t3", template_id="", title="Bio", due_date="2026-01-12"),
        ],
    )
    overview = build_onboarding_overview([employee], today)
    assert overview.total_overdue == 1
    assert overview.total_critical_overdue == 0
    assert overview.total_due_today == 1
    assert overview.total_due_soon == 1
    assert overview.employee_summaries[0].employee_name == "Taylor"


def test_build_onboarding_overview_tracks_critical_overdue_counts():
    today = date(2026, 1, 10)
    employee = Employee(
        id="emp1",
        name="Jordan",
        acceptance_date="2026-01-01",
        start_date="2026-01-10",
        tasks=[
            EmployeeTask(id="t1", template_id="", title="Critical", due_date="2026-01-09", critical=True),
            EmployeeTask(id="t2", template_id="", title="Normal", due_date="2026-01-09", critical=False),
        ],
    )
    overview = build_onboarding_overview([employee], today)
    assert overview.total_overdue == 2
    assert overview.total_critical_overdue == 1
    assert overview.employee_summaries[0].critical_overdue == 1
