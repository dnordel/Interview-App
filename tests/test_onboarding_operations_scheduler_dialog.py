from __future__ import annotations

from pathlib import Path

import onboarding_operations


def test_scheduler_opt_in_prefers_enabled_then_legacy_opt_in():
    assert onboarding_operations.scheduler_opt_in({"enabled": True, "opt_in": False}) is True
    assert onboarding_operations.scheduler_opt_in({"enabled": False, "opt_in": True}) is False
    assert onboarding_operations.scheduler_opt_in({"opt_in": True}) is True
    assert onboarding_operations.scheduler_opt_in({}) is False


def test_scheduler_expected_interval_hours_supports_new_and_legacy_settings():
    assert onboarding_operations.scheduler_expected_interval_hours({"expected_interval_hours": "6"}) == 6
    assert onboarding_operations.scheduler_expected_interval_hours({"expected_interval_hours": 0}) == 24
    assert onboarding_operations.scheduler_expected_interval_hours({"run_interval_minutes": 180}) == 3
    assert onboarding_operations.scheduler_expected_interval_hours({"run_interval_minutes": 30}) == 1
    assert onboarding_operations.scheduler_expected_interval_hours({}) == 24


def test_scheduler_status_text_uses_result_error_message_then_fallback():
    assert onboarding_operations.scheduler_status_text({}) == "No scheduler-triggered run status recorded yet."
    assert onboarding_operations.scheduler_status_text({"last_scheduler_result": " success "}) == "success"
    assert onboarding_operations.scheduler_status_text({"last_result": "failed"}) == "failed"
    assert onboarding_operations.scheduler_status_text({"last_error": "smtp unavailable"}) == "Error: smtp unavailable"
    assert onboarding_operations.scheduler_status_text({"message": "waiting"}) == "waiting"
    assert (
        onboarding_operations.scheduler_status_text({"last_scheduler_result": " "})
        == "Scheduler status is available but no result text was provided."
    )


def test_scheduler_command_example_renders_windows_task_command():
    script_path = Path("C:/Apps/onboarding_operations.py")

    command = onboarding_operations.scheduler_command_example(script_path)

    assert "schtasks /Create" in command
    assert "OnboardingReminderRunner" in command
    assert '--run-reminders' in command
    assert str(script_path) in command


def test_scheduler_script_path_defaults_to_canonical_onboarding_operations() -> None:
    assert Path(onboarding_operations.scheduler_script_path()).name == "onboarding_operations.py"
