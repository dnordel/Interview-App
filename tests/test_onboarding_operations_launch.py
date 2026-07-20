from __future__ import annotations

import onboarding_operations
from onboarding_operations import LaunchEmployeeSeed


def test_build_launch_context_normalizes_values_and_optional_seed():
    seed = LaunchEmployeeSeed(name="Dana Teacher", school="North", start_date="2026-08-15")

    context = onboarding_operations.build_launch_context(
        employee_id="  employee-1  ",
        urgent_only=1,
        employee_seed=seed,
    )

    assert context == {
        "employee_id": "employee-1",
        "urgent_only": True,
        "employee_seed": {
            "name": "Dana Teacher",
            "school": "North",
            "acceptance_date": "",
            "start_date": "2026-08-15",
        },
    }
    assert onboarding_operations.build_launch_context(employee_seed=LaunchEmployeeSeed()) == {
        "employee_id": "",
        "urgent_only": False,
    }


def test_launch_context_file_round_trips_and_fails_closed():
    payload = {"employee_id": "employee-1", "urgent_only": True}

    path = onboarding_operations.write_launch_context_file(payload)

    assert path is not None
    try:
        assert onboarding_operations.read_launch_context_file(str(path)) == payload
        path.write_text("[", encoding="utf-8")
        assert onboarding_operations.read_launch_context_file(str(path)) == {}
    finally:
        path.unlink(missing_ok=True)

    assert onboarding_operations.read_launch_context_file(None) == {}
    assert onboarding_operations.read_launch_context_file(str(path)) == {}


def test_extract_employee_seed_returns_prefilled_seed_only():
    seed = onboarding_operations.extract_employee_seed(
        {"employee_seed": {"name": "Dana Teacher", "school": "North"}}
    )

    assert seed == LaunchEmployeeSeed(name="Dana Teacher", school="North")
    assert onboarding_operations.extract_employee_seed({"employee_seed": {}}) is None
    assert onboarding_operations.extract_employee_seed(None) is None
