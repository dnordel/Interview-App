from datetime import date

from onboarding_operations import build_dashboard_today_summary
from onboarding_operations import build_dashboard_kpi_chips, build_recommended_action, kpi_navigation_target
from onboarding_operations import Employee, EmployeeTask
from onboarding_operations import filter_for_dashboard_kpi, filtered_tasks


def _employees_for_dashboard() -> list[Employee]:
    return [
        Employee(
            id="emp-a",
            name="Ari",
            acceptance_date="2026-01-01",
            start_date="2026-01-10",
            tasks=[
                EmployeeTask(id="task-over", template_id="", title="Submit I9", due_date="2026-01-09"),
                EmployeeTask(id="task-future", template_id="", title="Orientation", due_date="2026-01-14"),
            ],
        ),
        Employee(
            id="emp-b",
            name="Bri",
            acceptance_date="2026-01-01",
            start_date="2026-01-10",
            tasks=[EmployeeTask(id="task-today", template_id="", title="Badge", due_date="2026-01-10")],
        ),
    ]


def test_pending_filter_excludes_completed_items_only():
    tasks = [
        EmployeeTask(id="open", template_id="", title="Open", due_date="2026-01-11"),
        EmployeeTask(id="done", template_id="", title="Done", due_date="2026-01-09", completed=True, completed_at="2026-01-09"),
    ]
    selected = filtered_tasks(tasks, date(2026, 1, 10), "pending")
    assert [task.id for task in selected] == ["open"]


def test_kpi_filter_entrypoints_cover_dashboard_keys():
    assert filter_for_dashboard_kpi("overdue") == "overdue"
    assert filter_for_dashboard_kpi("urgent") == "urgent"
    assert filter_for_dashboard_kpi("critical") == "urgent"
    assert filter_for_dashboard_kpi("pending") == "pending"


def test_dashboard_kpi_chips_and_navigation_targets_are_deterministic():
    employees = _employees_for_dashboard()
    summary = build_dashboard_today_summary([], employees, {"critical_window_days": 3}, today=date(2026, 1, 10))
    chips = build_dashboard_kpi_chips(summary, employees, date(2026, 1, 10))

    chip_counts = {chip.key: chip.count for chip in chips}
    assert chip_counts == {"overdue": 1, "due_today": 1, "urgent": 2, "pending": 3}

    target = kpi_navigation_target(employees, date(2026, 1, 10), "overdue")
    assert target is not None
    assert target["employee_id"] == "emp-a"
    assert target["filter_key"] == "overdue"


def test_recommended_action_prioritizes_overdue_then_due_today():
    employees = _employees_for_dashboard()
    summary = build_dashboard_today_summary([], employees, {"critical_window_days": 3}, today=date(2026, 1, 10))
    recommendation = build_recommended_action(summary, employees, date(2026, 1, 10))
    assert recommendation.action_key == "open_filtered_tasks"
    assert recommendation.filter_key == "overdue"

    for task in employees[0].tasks:
        task.completed = True
        task.completed_at = "2026-01-10"
    summary_without_overdue = build_dashboard_today_summary([], employees, {"critical_window_days": 3}, today=date(2026, 1, 10))
    due_today_recommendation = build_recommended_action(summary_without_overdue, employees, date(2026, 1, 10))
    assert due_today_recommendation.filter_key == "due_today"
