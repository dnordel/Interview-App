from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_EXPECTED_INTERVAL_HOURS = 24


def scheduler_opt_in(settings: dict[str, Any]) -> bool:
    if "enabled" in settings:
        return bool(settings.get("enabled"))
    return bool(settings.get("opt_in", False))


def scheduler_expected_interval_hours(settings: dict[str, Any]) -> int:
    if "expected_interval_hours" in settings:
        return _positive_int(settings.get("expected_interval_hours"), DEFAULT_EXPECTED_INTERVAL_HOURS)

    if "run_interval_minutes" in settings:
        minutes = _positive_int(settings.get("run_interval_minutes"), DEFAULT_EXPECTED_INTERVAL_HOURS * 60)
        return max(1, minutes // 60)

    return DEFAULT_EXPECTED_INTERVAL_HOURS


def scheduler_status_text(status: dict[str, Any]) -> str:
    if not status:
        return "No scheduler-triggered run status recorded yet."

    result = status.get("last_scheduler_result")
    if not isinstance(result, str) or not result.strip():
        result = status.get("last_result")

    if isinstance(result, str) and result.strip():
        return result.strip()

    error = status.get("last_error")
    if isinstance(error, str) and error.strip():
        return f"Error: {error.strip()}"

    message = status.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return "Scheduler status is available but no result text was provided."


def scheduler_script_path(script_path: str | Path | None = None) -> str:
    candidate = Path(script_path) if script_path else Path(__file__).resolve().with_name("onboarding_app.pyw")
    return str(candidate)


def scheduler_command_example(script_path: str | Path | None = None) -> str:
    script = scheduler_script_path(script_path)
    return f'schtasks /Create /SC HOURLY /MO 1 /TN "OnboardingReminderRunner" /TR "pythonw \\\"{script}\\\" --run-reminders" /F'


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback
